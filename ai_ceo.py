"""
AI CEO for Dropshipping Business
Uses Groq/Qwen to make business decisions
Keys are stored in the app database - NOT in environment variables
"""

import json
import requests
from datetime import datetime


class AICEO:
    """The AI CEO that runs the business"""

    def __init__(self, api_keys=None, active_provider='qwen'):
        self.api_keys = api_keys or {}
        self.active_provider = active_provider
        self.decisions = []
        self.tasks = []

    def think(self, prompt):
        """Make a business decision"""
        system_prompt = """You are the CEO of a dropshipping business.
Your job is to make smart business decisions, create marketing strategies,
analyze data, and request features from the development team.

You have access to:
- Order data
- Product performance
- Marketing tools
- Customer feedback

When you need something built, make API calls to request features.
Be concise and decisive."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]

        providers = [self.active_provider] + [p for p in ['groq', 'qwen'] if p != self.active_provider]

        for provider in providers:
            key = self.api_keys.get(f'{provider}_key', '')
            if not key:
                continue
            try:
                if provider == 'groq':
                    response = requests.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": "llama-3.3-70b-versatile", "messages": messages, "max_tokens": 500},
                        timeout=30
                    )
                elif provider == 'qwen':
                    response = requests.post(
                        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": "qwen-plus", "messages": messages, "max_tokens": 500},
                        timeout=30
                    )
                else:
                    continue

                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content']
            except Exception as e:
                print(f"{provider} error: {e}")

        return "AI unavailable - please configure your API keys in Settings"

    def analyze_performance(self, orders, products):
        prompt = f"""Analyze this data and tell me:
1. What's working
2. What's not working
3. 3 specific recommendations

Orders: {len(orders)}
Products: {len(products)}

Data: {json.dumps({'orders': orders[:5], 'products': products[:5]})}"""
        return self.think(prompt)

    def create_marketing_plan(self):
        prompt = """Create a 7-day marketing plan for a dropshipping business.
Include:
- Social media strategy
- Ad budget recommendations
- Content ideas
- Target audience"""
        return self.think(prompt)

    def decide(self, situation):
        prompt = f"""Situation: {situation}

Make a decision and explain your reasoning.
If you need something built, say 'BUILD:' followed by what you need."""
        result = self.think(prompt)
        self.decisions.append({
            'situation': situation,
            'decision': result,
            'time': datetime.now().isoformat()
        })
        return result
