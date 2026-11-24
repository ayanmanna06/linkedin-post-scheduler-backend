from flask import jsonify, request
from pydantic import BaseModel, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from backend.services.exceptions import ValidationError
from backend.services.llm_service import llm_service

# --- Request Validation Schemas ---

class AIGenerateSchema(BaseModel):
    topic: str = Field(..., min_length=2, max_length=500, description="Brief topic or key takeaway")
    tone: str = Field(
        default="professional",
        description="professional, friendly, thought-leadership, storytelling, or promotional",
    )
    audience: str = Field(
        default="General LinkedIn Professionals",
        max_length=200,
        description="Target audience or niche",
    )
    length: str = Field(
        default="medium",
        description="short, medium, or long",
    )
    language: str = Field(
        default="English",
        max_length=50,
        description="Language for the post output",
    )
    keywords: list[str] | None = Field(
        default=None,
        description="Optional list of key concepts, hashtags, or tags to weave in",
    )

    @field_validator("tone")
    @classmethod
    def validate_tone(cls, v: str) -> str:
        valid_tones = ("professional", "hiring", "friendly", "thought-leadership", "storytelling", "promotional")
        if v.lower() not in valid_tones:
            raise ValueError(f"Tone must be one of: {', '.join(valid_tones)}")
        return v.lower()

    @field_validator("length")
    @classmethod
    def validate_length(cls, v: str) -> str:
        valid_lengths = ("short", "medium", "long")
        if v.lower() not in valid_lengths:
            raise ValueError(f"Length must be one of: {', '.join(valid_lengths)}")
        return v.lower()


class AIRewriteSchema(BaseModel):
    post_text: str = Field(..., min_length=5, max_length=3000, description="Existing draft text to modify")
    mode: str = Field(
        default="regenerate",
        description="shorter, more_formal, more_engaging, or regenerate",
    )
    instructions: str | None = Field(None, max_length=300, description="Optional extra instructions")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        valid_modes = ("shorter", "more_formal", "more_engaging", "regenerate")
        if v.lower() not in valid_modes:
            raise ValueError(f"Rewrite mode must be one of: {', '.join(valid_modes)}")
        return v.lower()


# --- Controller ---

class AIController:
    @staticmethod
    def generate_post():
        data = request.get_json(silent=True) or {}
        try:
            schema = AIGenerateSchema(**data)
        except PydanticValidationError as e:
            raise ValidationError("Invalid AI generation brief.", details=e.errors()) from e

        result = llm_service.generate_post(
            topic=schema.topic,
            tone=schema.tone,
            audience=schema.audience,
            length=schema.length,
            language=schema.language,
            keywords=schema.keywords,
        )
        return jsonify(
            {
                "data": {
                    "draft": result["draft"],
                    "character_count": result["character_count"],
                    "brief": result["brief"],
                    "tags": result.get("tags", []),
                    "message": "AI draft generated successfully. Review and edit before scheduling.",
                }
            }
        )

    @staticmethod
    def rewrite_post():
        data = request.get_json(silent=True) or {}
        try:
            schema = AIRewriteSchema(**data)
        except PydanticValidationError as e:
            raise ValidationError("Invalid rewrite payload.", details=e.errors()) from e

        result = llm_service.rewrite_post(
            original_post=schema.post_text,
            mode=schema.mode,
            instructions=schema.instructions,
        )
        return jsonify(
            {
                "data": {
                    "draft": result["draft"],
                    "character_count": result["character_count"],
                    "mode": result["mode"],
                }
            }
        )


ai_controller = AIController()
