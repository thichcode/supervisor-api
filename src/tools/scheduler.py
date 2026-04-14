"""
Scheduler - Cron job scheduler for automated tasks
Supports cron expressions, intervals, and one-time jobs
"""

import asyncio
import json
import uuid
import hashlib
from typing import Optional, List, Dict, Any, Callable, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import structlog

logger = structlog.get_logger()

# Try importing croniter
CRONITER_AVAILABLE = False
try:
    from croniter import croniter
    CRONITER_AVAILABLE = True
except ImportError:
    pass


class JobStatus(str, Enum):
    """Job status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class JobType(str, Enum):
    """Job type"""
    CRON = "cron"           # Recurring based on cron expression
    INTERVAL = "interval"   # Recurring based on interval
    ONCE = "once"           # One-time job
    DELAYED = "delayed"     # Delayed one-time job


@dataclass
class Job:
    """Scheduled job definition"""
    id: str
    name: str
    job_type: JobType
    func_name: str          # Function to call
    func_args: tuple = field(default_factory=tuple)
    func_kwargs: Dict = field(default_factory=dict)
    
    # Scheduling
    cron_expr: Optional[str] = None
    interval_seconds: Optional[int] = None
    run_at: Optional[datetime] = None
    
    # Config
    enabled: bool = True
    max_runs: Optional[int] = None  # None = unlimited
    timeout: Optional[float] = None  # seconds
    
    # Status
    status: JobStatus = JobStatus.PENDING
    run_count: int = 0
    last_run: Optional[datetime] = None
    last_status: Optional[JobStatus] = None
    last_result: Optional[Any] = None
    last_error: Optional[str] = None
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def __post_init__(self):
        if self.id is None:
            self.id = str(uuid.uuid4())[:8]
    
    def get_next_run(self) -> Optional[datetime]:
        """Get next scheduled run time"""
        now = datetime.utcnow()
        
        if self.job_type == JobType.CRON and self.cron_expr:
            if CRONITER_AVAILABLE:
                cron = croniter(self.cron_expr, now)
                return cron.get_next(datetime)
        
        elif self.job_type == JobType.INTERVAL and self.interval_seconds:
            if self.last_run:
                return self.last_run + timedelta(seconds=self.interval_seconds)
            return now
        
        elif self.job_type == JobType.ONCE or self.job_type == JobType.DELAYED:
            return self.run_at
        
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict"""
        return {
            "id": self.id,
            "name": self.name,
            "job_type": self.job_type.value,
            "func_name": self.func_name,
            "schedule": self.cron_expr or f"{self.interval_seconds}s" if self.interval_seconds else self.run_at.isoformat(),
            "enabled": self.enabled,
            "status": self.status.value,
            "run_count": self.run_count,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_status": self.last_status.value if self.last_status else None,
            "last_error": self.last_error,
            "next_run": self.get_next_run().isoformat() if self.get_next_run() else None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class JobResult:
    """Result of a job execution"""
    job_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    status: JobStatus
    result: Any
    error: Optional[str]
    elapsed_ms: float


class Scheduler:
    """
    In-memory job scheduler
    Supports cron expressions and interval-based jobs
    """
    
    def __init__(
        self,
        max_concurrent: int = 5,
        timezone: str = "UTC",
    ):
        self.jobs: Dict[str, Job] = {}
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._callbacks: Dict[str, Callable] = {}  # func_name -> callback
        self._history: List[JobResult] = []
        self._max_history = 100
    
    def register_callback(self, func_name: str, callback: Callable):
        """Register a function callback"""
        self._callbacks[func_name] = callback
        logger.info("Registered job callback", func_name=func_name)
    
    def add_cron_job(
        self,
        name: str,
        func_name: str,
        cron_expr: str,
        func_args: tuple = (),
        func_kwargs: Optional[Dict] = None,
        enabled: bool = True,
        max_runs: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Job:
        """
        Add a cron job
        
        Args:
            name: Job name
            func_name: Registered function name
            cron_expr: Cron expression (e.g., "0 * * * *" for hourly)
            func_args: Function arguments
            func_kwargs: Function keyword arguments
            enabled: Start enabled
            max_runs: Max times to run (None = unlimited)
            timeout: Timeout in seconds
        """
        job = Job(
            id=str(uuid.uuid4())[:8],
            name=name,
            job_type=JobType.CRON,
            func_name=func_name,
            func_args=func_args,
            func_kwargs=func_kwargs or {},
            cron_expr=cron_expr,
            enabled=enabled,
            max_runs=max_runs,
            timeout=timeout,
        )
        
        self.jobs[job.id] = job
        logger.info("Added cron job", job_id=job.id, name=name, cron=cron_expr)
        
        return job
    
    def add_interval_job(
        self,
        name: str,
        func_name: str,
        interval_seconds: int,
        func_args: tuple = (),
        func_kwargs: Optional[Dict] = None,
        enabled: bool = True,
        max_runs: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Job:
        """Add an interval-based job"""
        job = Job(
            id=str(uuid.uuid4())[:8],
            name=name,
            job_type=JobType.INTERVAL,
            func_name=func_name,
            func_args=func_args,
            func_kwargs=func_kwargs or {},
            interval_seconds=interval_seconds,
            enabled=enabled,
            max_runs=max_runs,
            timeout=timeout,
        )
        
        self.jobs[job.id] = job
        logger.info("Added interval job", job_id=job.id, name=name, interval=f"{interval_seconds}s")
        
        return job
    
    def add_once_job(
        self,
        name: str,
        func_name: str,
        run_at: datetime,
        func_args: tuple = (),
        func_kwargs: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> Job:
        """Add a one-time job"""
        job = Job(
            id=str(uuid.uuid4())[:8],
            name=name,
            job_type=JobType.ONCE,
            func_name=func_name,
            func_args=func_args,
            func_kwargs=func_kwargs or {},
            run_at=run_at,
            timeout=timeout,
        )
        
        self.jobs[job.id] = job
        logger.info("Added one-time job", job_id=job.id, name=name, run_at=run_at.isoformat())
        
        return job
    
    def add_delayed_job(
        self,
        name: str,
        func_name: str,
        delay_seconds: int,
        func_args: tuple = (),
        func_kwargs: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> Job:
        """Add a delayed job"""
        run_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
        return self.add_once_job(name, func_name, run_at, func_args, func_kwargs, timeout)
    
    def remove_job(self, job_id: str) -> bool:
        """Remove a job"""
        if job_id in self.jobs:
            job = self.jobs[job_id]
            
            # Stop if running
            if job_id in self._tasks:
                self._tasks[job_id].cancel()
                del self._tasks[job_id]
            
            del self.jobs[job_id]
            logger.info("Removed job", job_id=job_id, name=job.name)
            return True
        
        return False
    
    def pause_job(self, job_id: str) -> bool:
        """Pause a job"""
        if job_id in self.jobs:
            self.jobs[job_id].enabled = False
            self.jobs[job_id].status = JobStatus.PAUSED
            logger.info("Paused job", job_id=job_id)
            return True
        return False
    
    def resume_job(self, job_id: str) -> bool:
        """Resume a paused job"""
        if job_id in self.jobs:
            self.jobs[job_id].enabled = True
            self.jobs[job_id].status = JobStatus.PENDING
            logger.info("Resumed job", job_id=job_id)
            return True
        return False
    
    async def start(self):
        """Start the scheduler"""
        if self._running:
            return
        
        self._running = True
        logger.info("Scheduler started", jobs=len(self.jobs))
        
        while self._running:
            try:
                await self._run_pending_jobs()
                await asyncio.sleep(1)  # Check every second
            except Exception as e:
                logger.error("Scheduler error", error=str(e))
                await asyncio.sleep(5)
    
    async def stop(self):
        """Stop the scheduler"""
        self._running = False
        
        # Cancel all running tasks
        for task in self._tasks.values():
            task.cancel()
        
        self._tasks = {}
        logger.info("Scheduler stopped")
    
    async def _run_pending_jobs(self):
        """Check and run pending jobs"""
        now = datetime.utcnow()
        
        for job in self.jobs.values():
            if not job.enabled:
                continue
            
            if job.status == JobStatus.RUNNING:
                continue
            
            # Check max runs
            if job.max_runs and job.run_count >= job.max_runs:
                job.enabled = False
                job.status = JobStatus.COMPLETED
                continue
            
            # Check next run time
            next_run = job.get_next_run()
            if next_run and next_run <= now:
                # Run the job
                if job.id not in self._tasks or self._tasks[job.id].done():
                    task = asyncio.create_task(self._run_job(job))
                    self._tasks[job.id] = task
    
    async def _run_job(self, job: Job):
        """Execute a job"""
        started_at = datetime.utcnow()
        job.status = JobStatus.RUNNING
        job.last_run = started_at
        
        result = None
        error = None
        status = JobStatus.COMPLETED
        
        try:
            async with self._semaphore:
                callback = self._callbacks.get(job.func_name)
                
                if not callback:
                    raise ValueError(f"Callback not found: {job.func_name}")
                
                if job.timeout:
                    result = await asyncio.wait_for(
                        callback(*job.func_args, **job.func_kwargs),
                        timeout=job.timeout
                    )
                else:
                    result = await callback(*job.func_args, **job.func_kwargs)
                
                logger.info("Job completed", job_id=job.id, name=job.name)
        
        except asyncio.TimeoutError:
            error = f"Job timed out after {job.timeout}s"
            status = JobStatus.FAILED
            logger.error("Job timed out", job_id=job.id, timeout=job.timeout)
        
        except Exception as e:
            error = str(e)
            status = JobStatus.FAILED
            logger.error("Job failed", job_id=job.id, error=str(e))
        
        completed_at = datetime.utcnow()
        elapsed_ms = (completed_at - started_at).total_seconds() * 1000
        
        # Update job status
        job.status = status
        job.last_status = status
        job.last_result = result
        job.last_error = error
        job.run_count += 1
        job.updated_at = completed_at
        
        # One-time jobs should be disabled after running
        if job.job_type in (JobType.ONCE, JobType.DELAYED):
            job.enabled = False
        
        # Store result in history
        job_result = JobResult(
            job_id=job.id,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            result=result,
            error=error,
            elapsed_ms=elapsed_ms,
        )
        self._history.append(job_result)
        
        # Trim history
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
    
    def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID"""
        return self.jobs.get(job_id)
    
    def list_jobs(
        self,
        status: Optional[JobStatus] = None,
        job_type: Optional[JobType] = None,
        enabled: Optional[bool] = None,
    ) -> List[Job]:
        """List jobs with filters"""
        jobs = list(self.jobs.values())
        
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        
        if job_type is not None:
            jobs = [j for j in jobs if j.job_type == job_type]
        
        if enabled is not None:
            jobs = [j for j in jobs if j.enabled == enabled]
        
        return jobs
    
    def get_history(
        self,
        job_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get job execution history"""
        history = self._history
        
        if job_id:
            history = [h for h in history if h.job_id == job_id]
        
        return [
            {
                "job_id": h.job_id,
                "started_at": h.started_at.isoformat(),
                "completed_at": h.completed_at.isoformat() if h.completed_at else None,
                "status": h.status.value,
                "elapsed_ms": h.elapsed_ms,
                "error": h.error,
            }
            for h in history[-limit:]
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get scheduler statistics"""
        return {
            "total_jobs": len(self.jobs),
            "enabled_jobs": sum(1 for j in self.jobs.values() if j.enabled),
            "running_jobs": sum(1 for j in self.jobs.values() if j.status == JobStatus.RUNNING),
            "jobs_by_type": {
                t.value: sum(1 for j in self.jobs.values() if j.job_type == t)
                for t in JobType
            },
            "jobs_by_status": {
                s.value: sum(1 for j in self.jobs.values() if j.status == s)
                for s in JobStatus
            },
            "total_runs": sum(j.run_count for j in self.jobs.values()),
            "history_size": len(self._history),
        }


# Global scheduler instance
_scheduler: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    """Get or create global scheduler"""
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


# Predefined schedules
def schedule_report_generation(scheduler: Scheduler):
    """Schedule daily IT report generation"""
    scheduler.add_cron_job(
        name="Daily IT Report",
        func_name="generate_it_report",
        cron_expr="0 8 * * *",  # 8 AM daily
        func_kwargs={"report_type": "daily"},
    )
    
    scheduler.add_cron_job(
        name="Weekly IT Report",
        func_name="generate_it_report",
        cron_expr="0 8 * * 1",  # 8 AM every Monday
        func_kwargs={"report_type": "weekly"},
    )


def schedule_data_sync(scheduler: Scheduler, interval_seconds: int = 300):
    """Schedule periodic data sync"""
    scheduler.add_interval_job(
        name="Sync Trino Data",
        func_name="sync_trino_data",
        interval_seconds=interval_seconds,
        timeout=60.0,
    )


# Cron expression helper
CRON_EXAMPLES = {
    "every_minute": "* * * * *",
    "every_5_minutes": "*/5 * * * *",
    "every_15_minutes": "*/15 * * * *",
    "every_hour": "0 * * * *",
    "every_day_8am": "0 8 * * *",
    "every_day_midnight": "0 0 * * *",
    "every_week_monday_8am": "0 8 * * 1",
    "every_month_1st": "0 0 1 * *",
}
