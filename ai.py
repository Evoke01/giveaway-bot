"""
ai.py — Interfaces with Nvidia NIM (via OpenAI library) to solve CAPTCHAs.
"""

import base64
import logging
from typing import Optional

from openai import AsyncOpenAI

import config

log = logging.getLogger(__name__)

# Initialize the async client with Nvidia's base URL
client = AsyncOpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=config.NVIDIA_API_KEY,
)


async def solve_captcha(image_bytes: bytes, prompt_text: str, button_labels: list[str]) -> Optional[str]:
    """
    Pass the image and the button labels to the Vision model to pick the correct answer.
    Returns the exact button label to click.
    """
    if not config.NVIDIA_API_KEY:
        log.warning("NVIDIA_API_KEY not set. Cannot solve CAPTCHA.")
        return None

    if not button_labels:
        return None

    b64_image = base64.b64encode(image_bytes).decode('utf-8')
    # Using png/jpeg is fine, the API just needs the base64 payload.
    data_url = f"data:image/jpeg;base64,{b64_image}"

    # Strict prompt to ensure it only returns the label.
    prompt = (
        f"You are a CAPTCHA solving assistant. Look at the image and the question: '{prompt_text}'.\n"
        f"The available options are: {button_labels}\n"
        f"Based on the image, which option is correct? Reply ONLY with the exact text of the correct option, nothing else. Do not add quotes or punctuation."
    )

    try:
        # Llama 3.2 90B Vision Instruct is the standard NIM vision model
        response = await client.chat.completions.create(
            model="meta/llama-3.2-90b-vision-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url}
                        }
                    ]
                }
            ],
            temperature=0.1,
            max_tokens=20,
        )
        answer = response.choices[0].message.content.strip()
        log.info("AI CAPTCHA solver responded: %r", answer)

        # Fuzzy match the AI's answer back to the exact button label
        answer_lower = answer.lower()
        for label in button_labels:
            if answer_lower in label.lower() or label.lower() in answer_lower:
                return label

        # Fallback to exact
        return answer
    except Exception as exc:
        log.error("AI CAPTCHA solver failed: %s", exc)
        return None
