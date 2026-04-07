"""
OpenTelemetry Distributed Tracing
Provides distributed tracing across all services and LLM calls
"""
import time
from contextvars import ContextVar
from functools import wraps
from typing import Optional, Callable, Any
from dataclasses import dataclass

# OpenTelemetry imports - optional dependency
try:
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.trace import Status, StatusCode, SpanKind
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None
    metrics = None

import structlog

logger = structlog.get_logger()

# Context for request tracing
_current_span: ContextVar[Optional[Any]] = ContextVar('current_span', default=None)


@dataclass
class TracingConfig:
    service_name: str = "supervisor-api"
    otlp_endpoint: Optional[str] = None
    console_export: bool = True
    sample_rate: float = 1.0
    enable_metrics: bool = True


class TracingManager:
    """Manages OpenTelemetry tracing and metrics"""
    
    def __init__(self, config: Optional[TracingConfig] = None):
        self.config = config or TracingConfig()
        self._tracer = None
        self._meter = None
        self._initialized = False
        self._propagator = TraceContextTextMapPropagator() if OTEL_AVAILABLE else None
        
    def initialize(self):
        """Initialize OpenTelemetry tracing"""
        if not OTEL_AVAILABLE:
            logger.warning("OpenTelemetry not available - tracing disabled")
            return
            
        if self._initialized:
            return
            
        try:
            # Create resource
            resource = Resource.create({
                SERVICE_NAME: self.config.service_name,
                "service.version": "1.0.0",
                "deployment.environment": "production",
            })
            
            # Set up tracing
            tracer_provider = TracerProvider(resource=resource)
            
            # Console exporter for development
            if self.config.console_export:
                console_processor = BatchSpanProcessor(ConsoleSpanExporter())
                tracer_provider.add_span_processor(console_processor)
            
            # OTLP exporter for production (if endpoint provided)
            if self.config.otlp_endpoint:
                try:
                    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                    otlp_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=self.config.otlp_endpoint))
                    tracer_provider.add_span_processor(otlp_processor)
                    logger.info("OTLP exporter configured", endpoint=self.config.otlp_endpoint)
                except ImportError:
                    logger.warning("OTLP exporter not available")
            
            trace.set_tracer_provider(tracer_provider)
            self._tracer = trace.get_tracer(self.config.service_name)
            
            # Set up metrics if enabled
            if self.config.enable_metrics:
                metric_reader = PeriodicExportingMetricReader(
                    ConsoleMetricExporter(),
                    export_interval_millis=60000
                )
                meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
                metrics.set_meter_provider(meter_provider)
                self._meter = metrics.get_meter(self.config.service_name)
            
            self._initialized = True
            logger.info("OpenTelemetry initialized", service=self.config.service_name)
            
        except Exception as e:
            logger.error("Failed to initialize OpenTelemetry", error=str(e))
    
    def get_tracer(self):
        """Get the tracer instance"""
        if not self._initialized:
            self.initialize()
        return self._tracer
    
    def get_meter(self):
        """Get the meter instance"""
        if not self._initialized:
            self.initialize()
        return self._meter
    
    def extract_context(self, headers: dict) -> Optional[Any]:
        """Extract tracing context from HTTP headers"""
        if not self._propagator:
            return None
        try:
            return self._propagator.extract(carrier=headers)
        except Exception:
            return None
    
    def inject_context(self, headers: dict) -> dict:
        """Inject tracing context into HTTP headers"""
        if not self._propagator:
            return headers
        try:
            self._propagator.inject(carrier=headers)
        except Exception:
            pass
        return headers


# Global tracing manager
tracing = TracingManager()


def traced(
    name: Optional[str] = None,
    kind: str = "internal",
    attributes: Optional[dict] = None
):
    """
    Decorator to add tracing to async functions
    
    Usage:
        @traced("process_request", "server", {"component": "api"})
        async def process_request():
            ...
    """
    def decorator(func: Callable) -> Callable:
        if not OTEL_AVAILABLE:
            return func
            
        span_name = name or func.__name__
        span_kind = {
            "server": SpanKind.SERVER,
            "client": SpanKind.CLIENT,
            "producer": SpanKind.PRODUCER,
            "consumer": SpanKind.CONSUMER,
            "internal": SpanKind.INTERNAL,
        }.get(kind, SpanKind.INTERNAL)
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = tracing.get_tracer()
            if not tracer:
                return await func(*args, **kwargs)
            
            span_attributes = attributes or {}
            span_attributes["function"] = func.__name__
            
            with tracer.start_as_current_span(
                span_name,
                kind=span_kind,
                attributes=span_attributes
            ) as span:
                _current_span.set(span)
                start_time = time.time()
                
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise
                finally:
                    duration = time.time() - start_time
                    span.set_attribute("duration_ms", int(duration * 1000))
                    _current_span.set(None)
                    
        return wrapper
    return decorator


def traced_llm_call(model: str, operation: str = "completion"):
    """Decorator specifically for LLM calls"""
    def decorator(func: Callable) -> Callable:
        if not OTEL_AVAILABLE:
            return func
            
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = tracing.get_tracer()
            if not tracer:
                return await func(*args, **kwargs)
            
            with tracer.start_as_current_span(
                f"llm.{operation}",
                kind=SpanKind.CLIENT,
                attributes={
                    "llm.model": model,
                    "llm.operation": operation,
                    "system": "openai",
                }
            ) as span:
                start_time = time.time()
                start_tokens = kwargs.get("start_tokens", 0)
                
                try:
                    result = await func(*args, **kwargs)
                    
                    # Record result metrics
                    if isinstance(result, tuple) and len(result) >= 2:
                        content, confidence = result[0], result[1]
                        span.set_attribute("llm.confidence", confidence)
                        span.set_attribute("llm.response_length", len(content))
                    
                    span.set_status(Status(StatusCode.OK))
                    return result
                    
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise
                finally:
                    duration = time.time() - start_time
                    span.set_attribute("llm.duration_ms", int(duration * 1000))
                    
        return wrapper
    return decorator


def traced_agent(agent_name: str):
    """Decorator for agent execution tracing"""
    def decorator(func: Callable) -> Callable:
        if not OTEL_AVAILABLE:
            return func
            
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = tracing.get_tracer()
            if not tracer:
                return await func(*args, **kwargs)
            
            with tracer.start_as_current_span(
                f"agent.{agent_name}",
                kind=SpanKind.INTERNAL,
                attributes={
                    "agent.name": agent_name,
                    "agent.type": "llm",
                }
            ) as span:
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    span.set_attribute("agent.completed", True)
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise
                    
        return wrapper
    return decorator


def traced_db_query(operation: str):
    """Decorator for database query tracing"""
    def decorator(func: Callable) -> Callable:
        if not OTEL_AVAILABLE:
            return func
            
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = tracing.get_tracer()
            if not tracer:
                return await func(*args, **kwargs)
            
            table_name = kwargs.get("table", "unknown")
            
            with tracer.start_as_current_span(
                f"db.{operation}",
                kind=SpanKind.CLIENT,
                attributes={
                    "db.system": "postgresql",
                    "db.operation": operation,
                    "db.table": table_name,
                }
            ) as span:
                start_time = time.time()
                
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    raise
                finally:
                    duration = time.time() - start_time
                    span.set_attribute("db.duration_ms", int(duration * 1000))
                    
        return wrapper
    return decorator


def get_current_span():
    """Get the current active span"""
    return _current_span.get()


def add_span_attribute(key: str, value: Any):
    """Add an attribute to the current span"""
    span = _current_span.get()
    if span:
        span.set_attribute(key, value)


def record_event(name: str, attributes: Optional[dict] = None):
    """Record an event in the current span"""
    span = _current_span.get()
    if span:
        span.add_event(name, attributes=attributes or {})


# Custom metrics for tracing
class TracingMetrics:
    """Custom metrics for distributed tracing"""
    
    def __init__(self):
        self._request_duration = None
        self._llm_duration = None
        self._db_duration = None
    
    def initialize(self, meter):
        """Initialize metrics with meter"""
        self._request_duration = meter.create_histogram(
            "http_request_duration_ms",
            "HTTP request duration in milliseconds",
            unit="ms"
        )
        self._llm_duration = meter.create_histogram(
            "llm_call_duration_ms", 
            "LLM call duration in milliseconds",
            unit="ms"
        )
        self._db_duration = meter.create_histogram(
            "db_query_duration_ms",
            "Database query duration in milliseconds",
            unit="ms"
        )
    
    def record_request_duration(self, duration_ms: float, attributes: dict = None):
        if self._request_duration:
            self._request_duration.record(duration_ms, attributes or {})
    
    def record_llm_duration(self, duration_ms: float, model: str):
        if self._llm_duration:
            self._llm_duration.record(duration_ms, {"model": model})
    
    def record_db_duration(self, duration_ms: float, operation: str, table: str):
        if self._db_duration:
            self._db_duration.record(duration_ms, {"operation": operation, "table": table})


tracing_metrics = TracingMetrics()
