"""
Validators - Input/Output validation tools
JSON Schema validation, output format standardization, data sanitization
"""

import re
import json
from typing import Optional, List, Dict, Any, Union, Callable, Type
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger()


class ValidationError(Exception):
    """Validation error with details"""
    def __init__(self, message: str, field: Optional[str] = None, errors: Optional[List[str]] = None):
        self.message = message
        self.field = field
        self.errors = errors or []
        super().__init__(self.message)


@dataclass
class ValidationResult:
    """Result of validation"""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    @property
    def error_message(self) -> str:
        if not self.errors:
            return ""
        if len(self.errors) == 1:
            return self.errors[0]
        return f"{len(self.errors)} errors: " + "; ".join(self.errors)
    
    def raise_if_invalid(self):
        """Raise exception if validation failed"""
        if not self.valid:
            raise ValidationError(self.error_message, errors=self.errors)


@dataclass
class FieldValidator:
    """Validator for a single field"""
    name: str
    field_type: Type
    required: bool = True
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    choices: Optional[List[Any]] = None
    custom_validator: Optional[Callable] = None
    default: Optional[Any] = None
    
    def validate(self, value: Any) -> ValidationResult:
        """Validate a value"""
        errors = []
        warnings = []
        
        # Check required
        if value is None:
            if self.required:
                errors.append(f"{self.name} is required")
                return ValidationResult(valid=False, errors=errors)
            return ValidationResult(valid=True)
        
        # Type check
        if self.field_type is not Any and not isinstance(value, self.field_type):
            # Try coercion for common types
            if self.field_type is int and isinstance(value, str):
                try:
                    value = int(value)
                except ValueError:
                    errors.append(f"{self.name} must be an integer")
                    return ValidationResult(valid=False, errors=errors)
            elif self.field_type is float and isinstance(value, (int, str)):
                try:
                    value = float(value)
                except ValueError:
                    errors.append(f"{self.name} must be a number")
                    return ValidationResult(valid=False, errors=errors)
            elif self.field_type is str and not isinstance(value, (str, int, float)):
                errors.append(f"{self.name} must be a string")
                return ValidationResult(valid=False, errors=errors)
        
        # String-specific validations
        if isinstance(value, str):
            if self.min_length and len(value) < self.min_length:
                errors.append(f"{self.name} must be at least {self.min_length} characters")
            
            if self.max_length and len(value) > self.max_length:
                warnings.append(f"{self.name} truncated to {self.max_length} characters")
                value = value[:self.max_length]
            
            if self.pattern:
                if not re.match(self.pattern, value):
                    errors.append(f"{self.name} format is invalid")
        
        # Number-specific validations
        if isinstance(value, (int, float)):
            if self.min_value is not None and value < self.min_value:
                errors.append(f"{self.name} must be at least {self.min_value}")
            
            if self.max_value is not None and value > self.max_value:
                errors.append(f"{self.name} must be at most {self.max_value}")
        
        # Choices validation
        if self.choices is not None and value not in self.choices:
            errors.append(f"{self.name} must be one of: {', '.join(map(str, self.choices))}")
        
        # Custom validator
        if self.custom_validator and not errors:
            try:
                self.custom_validator(value)
            except Exception as e:
                errors.append(f"{self.name}: {str(e)}")
        
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )


class SchemaValidator:
    """Multi-field schema validator"""
    
    def __init__(self, fields: Optional[List[FieldValidator]] = None):
        self.fields: Dict[str, FieldValidator] = {}
        if fields:
            for field in fields:
                self.fields[field.name] = field
    
    def add_field(self, field: FieldValidator):
        """Add a field validator"""
        self.fields[field.name] = field
    
    def validate(self, data: Dict[str, Any]) -> ValidationResult:
        """Validate data against schema"""
        all_errors = []
        all_warnings = []
        
        for name, validator in self.fields.items():
            value = data.get(name)
            result = validator.validate(value)
            
            all_errors.extend(result.errors)
            all_warnings.extend(result.warnings)
        
        return ValidationResult(
            valid=len(all_errors) == 0,
            errors=all_errors,
            warnings=all_warnings
        )
    
    def validate_with_defaults(self, data: Dict[str, Any]) -> tuple[Dict[str, Any], ValidationResult]:
        """Validate and apply defaults"""
        result = self.validate(data)
        
        # Apply defaults
        for name, validator in self.fields.items():
            if data.get(name) is None and validator.default is not None:
                data[name] = validator.default
        
        return data, result


# Pre-built validators
class CommonValidators:
    """Common validation patterns"""
    
    @staticmethod
    def email() -> FieldValidator:
        return FieldValidator(
            name="email",
            field_type=str,
            required=True,
            pattern=r'^[\w\.-]+@[\w\.-]+\.\w+$',
        )
    
    @staticmethod
    def phone() -> FieldValidator:
        return FieldValidator(
            name="phone",
            field_type=str,
            required=False,
            pattern=r'^[\d\s\-\+\(\)]+$',
            min_length=8,
            max_length=20,
        )
    
    @staticmethod
    def url() -> FieldValidator:
        return FieldValidator(
            name="url",
            field_type=str,
            required=False,
            pattern=r'^https?://[\w\.-]+(?:/[\w\.\-]*)*$',
        )
    
    @staticmethod
    def username() -> FieldValidator:
        return FieldValidator(
            name="username",
            field_type=str,
            required=True,
            pattern=r'^[\w\.-]{3,32}$',
        )
    
    @staticmethod
    def password(min_length: int = 8) -> FieldValidator:
        return FieldValidator(
            name="password",
            field_type=str,
            required=True,
            min_length=min_length,
        )
    
    @staticmethod
    def uuid() -> FieldValidator:
        return FieldValidator(
            name="uuid",
            field_type=str,
            required=False,
            pattern=r'^[\w]{8}-[\w]{4}-[\w]{4}-[\w]{4}-[\w]{12}$',
        )
    
    @staticmethod
    def ipv4() -> FieldValidator:
        return FieldValidator(
            name="ip_address",
            field_type=str,
            required=False,
            pattern=r'^(\d{1,3}\.){3}\d{1,3}$',
        )
    
    @staticmethod
    def json_string() -> FieldValidator:
        def validate_json(value: str):
            try:
                json.loads(value)
            except json.JSONDecodeError:
                raise ValueError("Must be valid JSON")
        
        return FieldValidator(
            name="json",
            field_type=str,
            required=False,
            custom_validator=validate_json,
        )


class DataSanitizer:
    """Data sanitization utilities"""
    
    @staticmethod
    def sanitize_html(text: str) -> str:
        """Remove HTML tags"""
        return re.sub(r'<[^>]+>', '', text)
    
    @staticmethod
    def sanitize_sql(text: str) -> str:
        """Escape SQL special characters"""
        replacements = {
            "'": "''",
            "\\": "\\\\",
            "\n": "\\n",
            "\r": "\\r",
            "\x1a": "\\Z",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Make filename safe"""
        # Remove dangerous characters
        filename = re.sub(r'[^\w\s\-\.]', '', filename)
        # Replace spaces with underscores
        filename = re.sub(r'\s+', '_', filename)
        # Limit length
        if len(filename) > 255:
            name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
            filename = name[:255 - len(ext) - 1] + '.' + ext
        return filename
    
    @staticmethod
    def truncate(text: str, max_length: int, suffix: str = "...") -> str:
        """Truncate text with suffix"""
        if len(text) <= max_length:
            return text
        return text[:max_length - len(suffix)] + suffix
    
    @staticmethod
    def remove_pii(text: str) -> str:
        """Attempt to remove PII from text"""
        # Email
        text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL]', text)
        # Phone
        text = re.sub(r'[\+]?[(]?[0-9]{1,3}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{3,6}[-\s\.]?[0-9]{3,6}', '[PHONE]', text)
        # SSN-like
        text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]', text)
        # Credit card-like
        text = re.sub(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[CARD]', text)
        return text


class OutputFormatter:
    """Standardize output formats"""
    
    @staticmethod
    def success_response(
        data: Any,
        message: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Format success response"""
        response = {
            "success": True,
            "data": data,
        }
        if message:
            response["message"] = message
        if meta:
            response["meta"] = meta
        return response
    
    @staticmethod
    def error_response(
        message: str,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Format error response"""
        response = {
            "success": False,
            "error": {
                "message": message,
            }
        }
        if code:
            response["error"]["code"] = code
        if details:
            response["error"]["details"] = details
        return response
    
    @staticmethod
    def paginated_response(
        items: List[Any],
        page: int,
        page_size: int,
        total: int,
    ) -> Dict[str, Any]:
        """Format paginated response"""
        return {
            "success": True,
            "data": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
                "has_next": page * page_size < total,
                "has_prev": page > 1,
            }
        }
    
    @staticmethod
    def stream_response(
        chunk: Any,
        final: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Format streaming response chunk"""
        response = {
            "chunk": chunk,
            "final": final,
        }
        if meta:
            response["meta"] = meta
        return response


# JSON Schema validator (simple implementation)
class JSONSchemaValidator:
    """Simple JSON Schema validator"""
    
    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema
    
    def validate(self, data: Any) -> ValidationResult:
        """Validate data against schema"""
        errors = []
        
        try:
            self._validate_object(data, self.schema, "")
        except ValidationError as e:
            errors.append(str(e))
        
        return ValidationResult(valid=len(errors) == 0, errors=errors)
    
    def _validate_object(self, data: Any, schema: Dict, path: str):
        """Recursively validate object"""
        # Type validation
        schema_type = schema.get("type")
        if schema_type:
            if not self._check_type(data, schema_type):
                raise ValidationError(
                    f"{path}: expected {schema_type}, got {type(data).__name__}",
                    field=path
                )
        
        # Object-specific validations
        if schema_type == "object" and isinstance(data, dict):
            # Required properties
            for prop in schema.get("required", []):
                if prop not in data:
                    raise ValidationError(
                        f"{path}.{prop} is required",
                        field=f"{path}.{prop}"
                    )
            
            # Properties validation
            properties = schema.get("properties", {})
            for key, value in data.items():
                if key in properties:
                    self._validate_object(value, properties[key], f"{path}.{key}")
        
        # Array validation
        elif schema_type == "array" and isinstance(data, list):
            items_schema = schema.get("items", {})
            min_items = schema.get("minItems")
            max_items = schema.get("maxItems")
            
            if min_items and len(data) < min_items:
                raise ValidationError(
                    f"{path}: must have at least {min_items} items",
                    field=path
                )
            
            if max_items and len(data) > max_items:
                raise ValidationError(
                    f"{path}: must have at most {max_items} items",
                    field=path
                )
            
            for i, item in enumerate(data):
                self._validate_object(item, items_schema, f"{path}[{i}]")
        
        # String validations
        elif schema_type == "string" and isinstance(data, str):
            min_length = schema.get("minLength", 0)
            max_length = schema.get("maxLength")
            pattern = schema.get("pattern")
            
            if len(data) < min_length:
                raise ValidationError(
                    f"{path}: must be at least {min_length} characters",
                    field=path
                )
            
            if max_length and len(data) > max_length:
                raise ValidationError(
                    f"{path}: must be at most {max_length} characters",
                    field=path
                )
            
            if pattern and not re.match(pattern, data):
                raise ValidationError(
                    f"{path}: does not match pattern {pattern}",
                    field=path
                )
        
        # Number validations
        elif schema_type in ("number", "integer") and isinstance(data, (int, float)):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            
            if minimum is not None and data < minimum:
                raise ValidationError(
                    f"{path}: must be >= {minimum}",
                    field=path
                )
            
            if maximum is not None and data > maximum:
                raise ValidationError(
                    f"{path}: must be <= {maximum}",
                    field=path
                )
    
    def _check_type(self, data: Any, schema_type: Union[str, List[str]]) -> bool:
        """Check if data matches schema type"""
        if isinstance(schema_type, list):
            return any(self._check_type(data, t) for t in schema_type)
        
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        
        expected = type_map.get(schema_type)
        if expected is None:
            return True  # Unknown type, skip check
        
        return isinstance(data, expected)


# Quick validation helpers
def validate_email(email: str) -> bool:
    """Quick email validation"""
    return bool(re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email))


def validate_json(text: str) -> tuple[bool, Optional[Dict]]:
    """Quick JSON validation"""
    try:
        return True, json.loads(text)
    except json.JSONDecodeError:
        return False, None


def validate_uuid(text: str) -> bool:
    """Quick UUID validation"""
    return bool(re.match(r'^[\w]{8}-[\w]{4}-[\w]{4}-[\w]{4}-[\w]{12}$', text))


# Pre-defined schemas
SCHEMAS = {
    "chat_message": {
        "type": "object",
        "required": ["message"],
        "properties": {
            "message": {"type": "string", "minLength": 1, "maxLength": 10000},
            "user_id": {"type": "string"},
            "session_id": {"type": "string"},
        }
    },
    "approval_request": {
        "type": "object",
        "required": ["action", "user_id"],
        "properties": {
            "action": {"type": "string", "minLength": 1},
            "user_id": {"type": "string", "minLength": 1},
            "parameters": {"type": "object"},
            "risk_level": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        }
    },
    "n8n_action": {
        "type": "object",
        "required": ["action_name"],
        "properties": {
            "action_name": {"type": "string", "minLength": 1},
            "parameters": {"type": "object"},
            "user_id": {"type": "string"},
        }
    },
}


def validate(schema_name: str, data: Dict) -> ValidationResult:
    """Quick validation using predefined schema"""
    if schema_name not in SCHEMAS:
        return ValidationResult(False, [f"Unknown schema: {schema_name}"])
    
    validator = JSONSchemaValidator(SCHEMAS[schema_name])
    return validator.validate(data)
