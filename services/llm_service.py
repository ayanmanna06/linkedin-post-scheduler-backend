import json
import re
from typing import Any

import requests

from backend.config import settings
from backend.services.exceptions import AppException
from backend.services.logger import get_logger

logger = get_logger("llm_service")


class LLMServiceError(AppException):
    default_code = "LLM_SERVICE_ERROR"
    default_status_code = 502
    default_message = "AI post generation failed."


class LLMService:
    LENGTH_GUIDES = {
        "short": "between 50 and 100 words (concise and punchy)",
        "medium": "between 150 and 220 words (balanced and insightful)",
        "long": "between 250 and 380 words (in-depth narrative, under 2800 characters)",
    }

    TONE_GUIDES = {
        "professional": "polished, executive, articulate, and value-driven with structured takeaways and concrete unicode bullet points (•)",
        "hiring": "compelling recruitment announcement with role mission, key responsibilities, qualifications, and direct application instructions",
        "thought-leadership": "high-signal, analytical, authoritative engineering perspective challenging conventions with deep architecture insights",
        "storytelling": "narrative-driven personal or technical journey, the initial obstacle, turning point, lessons learned, and emotional takeaway",
        "promotional": "exciting feature or product announcement, highlighting problems solved, core capabilities, and high-converting call to action",
        "friendly": "approachable, conversational, warm, authentic peer-to-peer tone using relatable phrasing",
    }

    TONE_FORMAT_DIRECTIVES = {
        "professional": (
            "MANDATORY FORMAT BLUEPRINT (Professional & Standard):\n"
            "• Executive, clear, structured, and authoritative voice.\n"
            "• Strong opening sentence stating the core insight or achievement.\n"
            "• 3-4 structured technical takeaways using clean unicode bullet points (•).\n"
            "• Professional closing question or forward-looking thought.\n"
            "• 3-5 relevant hashtags at the bottom."
        ),
        "storytelling": (
            "MANDATORY FORMAT BLUEPRINT (Storytelling & Milestones):\n"
            "• Narrative arc: Hook with personal context -> The challenge or obstacle faced -> The breakthrough or realization -> The core milestone.\n"
            "• Share 3-4 key lessons learned from the journey (using • bullets).\n"
            "• Vulnerable, inspiring, and authentic tone that resonates emotionally with peers.\n"
            "• 3-5 relevant hashtags at the bottom."
        ),
        "thought-leadership": (
            "MANDATORY FORMAT BLUEPRINT (Thought Leadership):\n"
            "• Bold, visionary or analytical assertion in the opening line.\n"
            "• High-signal technical depth: dissect architectural trade-offs, scaling realities, or future industry trajectory.\n"
            "• 3-4 actionable principles or core takeaways (using • bullets).\n"
            "• Provocative, debate-sparking question for senior engineering leaders.\n"
            "• 3-5 relevant hashtags at the bottom."
        ),
        "friendly": (
            "MANDATORY FORMAT BLUEPRINT (Friendly & Authentic):\n"
            "• Warm, conversational, peer-to-peer voice (as if chatting with a close colleague).\n"
            "• Relatable and humble phrasing, avoiding corporate stiffness.\n"
            "• 3-4 key points or fun discoveries shared casually with clean bullets (•).\n"
            "• Welcoming call to action inviting friendly tips and comments.\n"
            "• 3-5 relevant hashtags at the bottom."
        ),
        "promotional": (
            "MANDATORY FORMAT BLUEPRINT (Product / Announcement):\n"
            "• High-energy launch hook highlighting the core problem solved.\n"
            "• 3-4 standout features, architecture capabilities, or benefits (using • bullets).\n"
            "• Clear, compelling call to action directing readers to explore, try, or comment.\n"
            "• 3-5 relevant hashtags at the bottom."
        ),
        "hiring": (
            "MANDATORY FORMAT BLUEPRINT (Hiring / Job Opening):\n"
            "• 'We are hiring! 🚀' or exciting opening for the role.\n"
            "• One crisp sentence on company mission and work model (Remote / Hybrid / Onsite).\n"
            "• What you'll do (3-4 responsibilities with • bullets).\n"
            "• What we're looking for (3-4 requirements with • bullets).\n"
            "• How to apply instructions.\n"
            "• 4-5 recruitment hashtags (#Hiring #TechJobs)."
        ),
    }

    def _build_system_prompt(self) -> str:
        return (
            "You are an elite LinkedIn ghostwriter and executive copywriter for industry leaders, technical professionals, and founders.\n"
            "Your objective is to craft high-impact, professional LinkedIn posts that faithfully execute the user's specific topic, voice, and personal intent.\n\n"
            "ABSOLUTE PROHIBITIONS (DO NOT VIOLATE):\n"
            "• NEVER use markdown bold/italic asterisks (like **bold** or *italic*). LinkedIn DOES NOT render markdown—asterisks appear as ugly raw formatting artifacts. Write plain, readable text.\n"
            "• NEVER start with clichéd rhetorical questions like 'Imagine if...', 'In the fast-paced world of...', 'Have you ever wondered...'.\n"
            "• NEVER write meta-commentary or preamble greetings like 'Here is your post:' or 'Sure, here is a draft:'. Output ONLY the final post text.\n"
            "• NEVER assume an engineering, technical, or developer topic is a company hiring ad unless the user explicitly requested a job recruitment post.\n"
            "• NEVER convert a user's personal learning achievement, certification, course, or project milestone into a corporate job opening.\n\n"
            "INTENT GUIDELINES BY TOPIC:\n"
            "1. CERTIFICATION, COURSE COMPLETION & LEARNING ACHIEVEMENTS (e.g. certificate, exam, credential, course, graduated, passed):\n"
            "   - Voice: First-person ('I am excited to share that I have completed...', 'Thrilled to announce my certification in...').\n"
            "   - Hook: Authentic milestone celebration emphasizing continuous technical growth and dedication.\n"
            "   - Key Competencies Mastered: 3-4 clean unicode bullet points (•) highlighting practical, high-value technical capabilities acquired.\n"
            "   - Real-World Application: Brief reflection on how these skills solve modern engineering problems.\n"
            "   - Closing & Community: Inspiring message encouraging peers, with an engaging question to foster discussion.\n"
            "   - Hashtags: Relevant technology and professional learning tags.\n\n"
            "2. TECHNICAL THOUGHT LEADERSHIP & ARCHITECTURE (e.g. scaling, databases, concurrency, design principles):\n"
            "   - Header Hook: A bold, high-signal technical assertion or proven insight from production experience.\n"
            "   - Body: 3-4 concrete lessons, architectural trade-offs, or battle-tested principles with clean unicode bullets (•).\n"
            "   - Closing: One genuine question for engineering peers to foster comments.\n"
            "   - Hashtags: 3-4 relevant industry tags.\n\n"
            "3. HIRING & RECRUITMENT (ONLY when tone is 'hiring' or topic explicitly states 'we are hiring', 'job opening', 'job vacancy'):\n"
            "   - Header: 'We are hiring! 🚀' or 'Exciting opportunity: We are looking for an exceptional [Role]!'\n"
            "   - Work Model: Remote / Hybrid / On-site and team vision in 1 crisp sentence.\n"
            "   - What you'll do (Responsibilities): 3-4 clean unicode bullet points (•).\n"
            "   - What we're looking for (Requirements): 3-4 clean unicode bullet points (•).\n"
            "   - How to apply: Clear instructions (email/link/comments).\n"
            "   - Hashtags: Relevant recruitment tags (#Hiring #Careers #TechJobs).\n\n"
            "4. GENERAL PROFESSIONAL & STORYTELLING:\n"
            "   - Narrative-driven, engaging hook, valuable takeaway, crisp short paragraphs.\n\n"
            "QUALITY RULES:\n"
            "• Short 1-2 line paragraphs with spacing for readability.\n"
            "• Clean unicode bullets (•).\n"
            "• Tasteful emojis used purposefully.\n"
            "• Strictly under 2800 characters."
        )

    def _build_user_prompt(
        self,
        topic: str,
        tone: str,
        audience: str,
        length: str,
        language: str = "English",
        keywords: list[str] | None = None,
    ) -> str:
        tone_desc = self.TONE_GUIDES.get(tone.lower(), tone)
        length_desc = self.LENGTH_GUIDES.get(length.lower(), "around 150-250 words")
        kw_str = ", ".join(keywords) if keywords else "None specified"

        topic_lower = topic.lower()

        # Strict check for hiring: only when explicit hiring phrases appear or tone is hiring
        hiring_patterns = [
            r"\bwe(?:'re| are)\s+hiring\b",
            r"\bnow hiring\b",
            r"\bhiring for\b",
            r"\bjob opening\b",
            r"\bjob vacancy\b",
            r"\bopen position\b",
            r"\bopen role\b",
            r"\bjoin our team\b",
            r"\blooking to hire\b",
        ]
        is_hiring = tone.lower() == "hiring" or any(re.search(p, topic_lower) for p in hiring_patterns)

        # Detect certification / learning achievement
        cert_patterns = [
            r"\bcertificat",
            r"\bcertif",
            r"\bcredential",
            r"\bcompleted\b",
            r"\bcompletion\b",
            r"\bpassed\b",
            r"\bgraduated\b",
            r"\bcourse\b",
            r"\blearning\b",
            r"\bexam\b",
        ]
        is_cert = any(re.search(p, topic_lower) for p in cert_patterns)

        specific_instruction = ""
        if is_cert and not is_hiring:
            specific_instruction = (
                "\nCRITICAL DIRECTIVE: The user is celebrating earning a CERTIFICATION, COURSE COMPLETION, or LEARNING MILESTONE. "
                "Write an authentic, inspiring first-person post ('I'm excited to share that I have completed...'). "
                "Highlight 3-4 key technical competencies mastered (using • bullets), reflect on practical application, "
                "and ask peers a discussion question. Do NOT write a hiring post or job description."
            )
        elif is_hiring:
            specific_instruction = (
                "\nCRITICAL DIRECTIVE: This is a JOB POST / HIRING ANNOUNCEMENT. "
                "Write an actual, ready-to-publish recruitment post: 'We are hiring! 🚀', "
                "Role title, Key Responsibilities (bullets), What We're Looking For (bullets), "
                "How to apply (email/link), and #Hiring hashtags."
            )

        tone_key = tone.lower()
        tone_blueprint = self.TONE_FORMAT_DIRECTIVES.get(tone_key, "")

        return (
            f"Write a professional LinkedIn post with these specifications:\n"
            f"- Topic: {topic}\n"
            f"- Target Audience: {audience}\n"
            f"- Mandatory Tone Directive: You MUST strictly write in '{tone}' style ({tone_desc}). Every paragraph must strictly align with this tone.\n"
            f"- Target Length: {length} ({length_desc}). Strictly adhere to this word length.\n"
            f"- Language: {language}\n"
            f"- Key points / Hashtags to include: {kw_str}\n"
            f"- Strict Formatting: NO markdown bold asterisks (**). Do NOT use markdown heading hashes (# Heading). Use clean text and unicode bullet points (•).\n"
            f"- Mandatory Hashtag Requirement: The very last line of the post MUST be 3 to 5 hashtags starting with the '#' symbol (e.g. #DataEngineering #Snowflake #CloudComputing #BigData #Tech). Every single hashtag MUST start with '#'. Never write keywords without the '#' symbol.\n"
            f"{tone_blueprint}\n"
            f"{specific_instruction}\n\n"
            f"Remember: Output ONLY the final clean post text."
        )

    def _build_rewrite_prompt(
        self,
        original_post: str,
        mode: str,
        instructions: str | None = None,
    ) -> str:
        mode_instructions = {
            "shorter": "Make the post much more concise and punchy. Cut fluff while preserving the hook, core insight, and CTA.",
            "more_formal": "Elevate the post to be more executive, formal, and authoritative in tone, suitable for senior leadership.",
            "more_engaging": "Increase engagement and curiosity. Strengthen the opening hook and make the closing question more provocative.",
            "regenerate": "Create a completely fresh and original take on the topic of this post.",
        }
        instruction_text = mode_instructions.get(mode, mode_instructions["regenerate"])
        if instructions:
            instruction_text += f" Additional guidance: {instructions}"

        return (
            f"Revise the following LinkedIn post according to this directive:\n"
            f"Directive: {instruction_text}\n\n"
            f"Original post:\n{original_post}\n\n"
            f"STRICT: Output ONLY the revised post text. No explanations or intro."
        )

    def _call_ollama_api(self, prompt: str, system: str | None = None) -> str:
        url = settings.LLM_API_URL
        payload: dict[str, Any] = {
            "model": settings.LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.6,
                "top_p": 0.9,
            },
        }
        if system:
            payload["system"] = system

        try:
            logger.info("llm_request_started", model=settings.LLM_MODEL, url=url)
            resp = requests.post(url, json=payload, timeout=settings.LLM_TIMEOUT_SECONDS)
        except requests.Timeout as e:
            logger.error("llm_request_timeout", timeout=settings.LLM_TIMEOUT_SECONDS)
            raise LLMServiceError(
                f"LLM generation timed out after {settings.LLM_TIMEOUT_SECONDS}s.",
                status_code=504,
            ) from e
        except requests.ConnectionError as e:
            logger.error("llm_request_connection_error", error=str(e), url=url)
            raise LLMServiceError(
                f"Could not connect to self-hosted LLM at {url}.",
                status_code=502,
            ) from e
        except requests.RequestException as e:
            logger.error("llm_request_exception", error=str(e))
            raise LLMServiceError(f"LLM request error: {str(e)}", status_code=502) from e

        if not resp.ok:
            logger.error("llm_bad_response", status_code=resp.status_code, body=resp.text)
            raise LLMServiceError(
                f"LLM server returned error ({resp.status_code}): {resp.text}",
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
            raw_response = data.get("response", "")
        except json.JSONDecodeError:
            raw_response = resp.text

        if not raw_response or not raw_response.strip():
            raise LLMServiceError("LLM returned an empty response.")

        return self._sanitize_output(raw_response)

    def _sanitize_output(self, text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            lines = cleaned.split("\n")
            if len(lines) >= 2:
                cleaned = "\n".join(lines[1:-1]).strip()

        intro_patterns = [
            r"^(Here(?:'s| is) (?:a|your) (?:draft|LinkedIn post|post)[^\n]*:\s*)",
            r"^(Sure! Here is [^\n]*:\s*)",
            r"^(Certainly! Here is [^\n]*:\s*)",
        ]
        for pattern in intro_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

        # Convert markdown bullet lists (* or -) to clean unicode bullets (•)
        cleaned = re.sub(r"^[\*\-]\s+", "• ", cleaned, flags=re.MULTILINE)

        # Remove markdown bold/italic asterisks since LinkedIn does not render markdown
        # e.g. **Text** -> Text, *Text* -> Text, __Text__ -> Text
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*([^*\n]+)\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
        # Remove any lingering stray asterisks so text is completely star-free
        cleaned = re.sub(r"\*+", "", cleaned)

        # Clean markdown headers like '### Header' or '# Header' while preserving '#Hashtag'
        # Multi-hash (## to ######) at line start is always a heading
        cleaned = re.sub(r"^#{2,6}\s*", "", cleaned, flags=re.MULTILINE)
        # Single hash (# Header) followed by whitespace is a heading, but #Hashtag is preserved
        cleaned = re.sub(r"^#\s+", "", cleaned, flags=re.MULTILINE)

        cleaned = cleaned.strip()

        if len(cleaned) > 3000:
            logger.warn("llm_output_truncated", original_length=len(cleaned))
            cleaned = cleaned[:2990].rsplit(" ", 1)[0] + "..."

        return cleaned

    def _parse_and_clean_tags(
        self, draft: str, topic: str = "", keywords: list[str] | None = None
    ) -> tuple[str, list[str]]:
        existing_tags = re.findall(r"#\w+", draft)
        if existing_tags:
            return draft, list(dict.fromkeys(existing_tags))

        lines = draft.rstrip().split("\n")
        trailing_keywords = []
        idx = len(lines) - 1

        while idx >= 0:
            line = lines[idx].strip()
            if not line:
                idx -= 1
                continue
            if len(line) <= 40 and not re.search(r"[.?!:;]$", line) and len(line.split()) <= 4:
                trailing_keywords.insert(0, line)
                idx -= 1
            else:
                break

        if trailing_keywords and len(trailing_keywords) >= 2:
            formatted_tags = []
            for kw in trailing_keywords:
                clean_kw = re.sub(r"[^a-zA-Z0-9\s]", "", kw)
                pascal = "".join(w.capitalize() for w in clean_kw.split())
                if pascal:
                    formatted_tags.append(f"#{pascal}")

            clean_body = "\n".join(lines[: idx + 1]).rstrip()
            final_draft = f"{clean_body}\n\n" + " ".join(formatted_tags)
            return final_draft, list(dict.fromkeys(formatted_tags))

        inferred_tags = []
        if keywords:
            for k in keywords:
                clean_k = re.sub(r"[^a-zA-Z0-9\s]", "", k)
                pascal = "".join(w.capitalize() for w in clean_k.split())
                if pascal:
                    inferred_tags.append(f"#{pascal}")

        if not inferred_tags and topic:
            stop_words = {"want", "post", "with", "this", "that", "from", "have", "complete", "completed", "linkedin"}
            words = [w for w in re.findall(r"[A-Za-z0-9]+", topic) if len(w) > 3 and w.lower() not in stop_words]
            for w in words[:4]:
                inferred_tags.append(f"#{w.capitalize()}")

        if not inferred_tags:
            inferred_tags = ["#Tech", "#ProfessionalGrowth", "#Innovation"]

        unique_inferred = list(dict.fromkeys(inferred_tags))[:5]
        clean_body = draft.rstrip()
        final_draft = f"{clean_body}\n\n" + " ".join(unique_inferred)
        return final_draft, unique_inferred

    def generate_post(
        self,
        topic: str,
        tone: str = "professional",
        audience: str = "General LinkedIn Professionals",
        length: str = "medium",
        language: str = "English",
        keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            topic=topic,
            tone=tone,
            audience=audience,
            length=length,
            language=language,
            keywords=keywords,
        )
        draft = self._call_ollama_api(prompt=user_prompt, system=system_prompt)
        draft, unique_tags = self._parse_and_clean_tags(draft=draft, topic=topic, keywords=keywords)

        return {
            "draft": draft,
            "character_count": len(draft),
            "tags": unique_tags,
            "brief": {
                "topic": topic,
                "tone": tone,
                "audience": audience,
                "length": length,
                "language": language,
                "keywords": keywords or [],
            },
        }

    def rewrite_post(
        self,
        original_post: str,
        mode: str = "regenerate",
        instructions: str | None = None,
    ) -> dict[str, Any]:
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_rewrite_prompt(
            original_post=original_post,
            mode=mode,
            instructions=instructions,
        )
        revised = self._call_ollama_api(prompt=user_prompt, system=system_prompt)

        return {
            "draft": revised,
            "character_count": len(revised),
            "mode": mode,
        }


llm_service = LLMService()
