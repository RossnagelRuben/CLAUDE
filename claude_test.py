

import anthropic

import os
from dotenv import load_dotenv
load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

response = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=80,
    messages=[
        {"role": "user", "content": "Respondé solo: Claude funcionando"}
    ]
)

print(response.content[0].text)
