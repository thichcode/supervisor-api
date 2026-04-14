"""
Supervisor Skills - Plugin architecture for reusable workflows

Skills are stored in ~/.supervisor/skills/
Each skill has: SKILL.md, references/, templates/, scripts/
"""

from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from datetime import datetime
import structlog

logger = structlog.get_logger()


# ============ Skill Definition ============

@dataclass
class Skill:
    """Skill definition"""
    name: str
    description: str
    category: str
    trigger_keywords: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    prompts: Dict[str, str] = field(default_factory=dict)
    references: Dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    enabled: bool = True


# ============ Built-in Skills ============

BUILTIN_SKILLS = {
    "it-report": Skill(
        name="it-report",
        description="Generate IT service reports (KPI, uptime, incidents)",
        category="productivity",
        trigger_keywords=["báo cáo", "report", "it report", "kpi"],
        steps=[
            "1. Load IT service data from database",
            "2. Calculate KPI metrics",
            "3. Generate HTML report",
            "4. Save to output directory"
        ],
        prompts={
            "generate": "Generate IT service report with KPIs: {kpis}"
        }
    ),
    "data-pipeline": Skill(
        name="data-pipeline",
        description="Process data from multiple sources (CSV, Excel, JSON)",
        category="data",
        trigger_keywords=["data", "process", "import", "export", "pipeline"],
        steps=[
            "1. Identify data sources",
            "2. Extract and validate data",
            "3. Transform as needed",
            "4. Load to destination"
        ],
        prompts={
            "process": "Process data from {sources}"
        }
    ),
    "knowledge-base": Skill(
        name="knowledge-base",
        description="Manage knowledge base (policies, FAQs, guides)",
        category="knowledge",
        trigger_keywords=["policy", "faq", "guide", "knowledge", "hướng dẫn"],
        steps=[
            "1. Determine knowledge type",
            "2. Search existing entries",
            "3. Create or update entry",
            "4. Index for search"
        ],
        prompts={
            "search": "Search knowledge base for: {query}",
            "create": "Create {type}: {title}"
        }
    ),
    "translation": Skill(
        name="translation",
        description="Vietnamese-English translation",
        category="language",
        trigger_keywords=["translate", "dịch", "tiếng anh", "tiếng việt"],
        steps=[
            "1. Detect source language",
            "2. Translate text",
            "3. Format output"
        ],
        prompts={
            "translate": "Translate to {target_lang}: {text}"
        }
    ),
    "ticket-assist": Skill(
        name="ticket-assist",
        description="Create and manage support tickets",
        category="service",
        trigger_keywords=["ticket", "support", "case", "vấn đề", "issue"],
        steps=[
            "1. Parse ticket request",
            "2. Classify priority and category",
            "3. Create ticket in system",
            "4. Assign to appropriate team"
        ],
        prompts={
            "create": "Create ticket: {title}\n{description}"
        }
    ),
}


# ============ Skill Manager ============

class SkillManager:
    """Manage skills - load, save, execute"""
    
    def __init__(self, skills_dir: Optional[Path] = None):
        if skills_dir is None:
            self.skills_dir = Path.home() / ".supervisor" / "skills"
        else:
            self.skills_dir = skills_dir
        
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        
        # Merge built-in and user skills
        self.skills: Dict[str, Skill] = BUILTIN_SKILLS.copy()
        self._load_user_skills()
    
    def _load_user_skills(self):
        """Load user-defined skills"""
        if not self.skills_dir.exists():
            return
        
        for skill_path in self.skills_dir.iterdir():
            if not skill_path.is_dir():
                continue
            
            skill_file = skill_path / "SKILL.md"
            if skill_file.exists():
                try:
                    skill = self._parse_skill_file(skill_file)
                    self.skills[skill.name] = skill
                except Exception as e:
                    logger.warning("Failed to load skill", skill=skill_path.name, error=str(e))
    
    def _parse_skill_file(self, path: Path) -> Skill:
        """Parse SKILL.md file"""
        content = path.read_text()
        
        # Simple YAML frontmatter parsing
        import re
        
        # Extract name
        name_match = re.search(r'name:\s*(\S+)', content)
        name = name_match.group(1) if name_match else path.parent.name
        
        # Extract description
        desc_match = re.search(r'description:\s*(.+)', content)
        description = desc_match.group(1).strip() if desc_match else ""
        
        # Extract category
        cat_match = re.search(r'category:\s*(\S+)', content)
        category = cat_match.group(1) if cat_match else "custom"
        
        # Extract trigger keywords
        triggers_match = re.search(r'trigger_keywords:\s*(.+)', content)
        trigger_keywords = []
        if triggers_match:
            trigger_keywords = [k.strip() for k in triggers_match.group(1).split(',')]
        
        return Skill(
            name=name,
            description=description,
            category=category,
            trigger_keywords=trigger_keywords
        )
    
    def list_skills(self, category: Optional[str] = None) -> List[Skill]:
        """List all available skills"""
        skills = list(self.skills.values())
        
        if category:
            skills = [s for s in skills if s.category == category]
        
        return sorted(skills, key=lambda x: x.name)
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name"""
        return self.skills.get(name)
    
    def match_skill(self, query: str) -> Optional[Skill]:
        """Match query to a skill by trigger keywords"""
        query_lower = query.lower()
        
        for skill in self.skills.values():
            if not skill.enabled:
                continue
            
            for keyword in skill.trigger_keywords:
                if keyword.lower() in query_lower:
                    return skill
        
        return None
    
    def create_skill(
        self,
        name: str,
        description: str,
        category: str = "custom",
        trigger_keywords: Optional[List[str]] = None
    ) -> Skill:
        """Create a new skill"""
        skill = Skill(
            name=name,
            description=description,
            category=category,
            trigger_keywords=trigger_keywords or []
        )
        
        # Save to file
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(f"""---
name: {name}
description: {description}
category: {category}
trigger_keywords: {', '.join(skill.trigger_keywords)}
---

# {name}

{description}

## Usage

Use `/skill load {name}` to load this skill.

## Steps

1. Define your steps here
""")
        
        self.skills[name] = skill
        return skill
    
    def enable_skill(self, name: str, enabled: bool = True):
        """Enable or disable a skill"""
        if name in self.skills:
            self.skills[name].enabled = enabled
    
    def delete_skill(self, name: str):
        """Delete a user skill (not built-in)"""
        if name in BUILTIN_SKILLS:
            return  # Can't delete built-in
        
        if name in self.skills:
            del self.skills[name]
            
            # Remove files
            skill_dir = self.skills_dir / name
            if skill_dir.exists():
                import shutil
                shutil.rmtree(skill_dir)


# ============ Global Instance ============

_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    """Get global skill manager"""
    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager


__all__ = [
    "Skill",
    "SkillManager",
    "BUILTIN_SKILLS",
    "get_skill_manager",
]