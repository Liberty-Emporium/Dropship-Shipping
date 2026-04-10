"""
AI CEO for Dropshipping Business
Uses Groq/Llama to make business decisions
"""

import os
import json
import requests
from datetime import datetime

# AI Configuration
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
Qwen_API_KEY = os.environ.get('QWEN_API_KEY', '')

class AICEO:
    """The AI CEO that runs the business"""
    
    def __init__(self, name="CEO"):
        self.name = name
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
        
        # Try Groq first
        if GROQ_API_KEY:
            try:
                response = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": messages,
                        "max_tokens": 500
                    }
                )
                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content']
            except Exception as e:
                print(f"Groq error: {e}")
        
        # Fallback to Qwen
        if Qwen_API_KEY:
            try:
                response = requests.post(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {Qwen_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "qwen-plus",
                        "messages": messages,
                        "max_tokens": 500
                    }
                )
                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content']
            except Exception as e:
                print(f"Qwen error: {e}")
        
        return "AI unavailable - please configure API keys"
    
    def analyze_performance(self, orders, products):
        """Analyze business performance"""
        prompt = f"""Analyze this data and tell me:
1. What's working
2. What's not working
3. 3 specific recommendations

Orders: {len(orders)}
Products: {len(products)}

Data: {json.dumps({'orders': orders[:5], 'products': products[:5]})}"""
        
        return self.think(prompt)
    
    def create_marketing_plan(self):
        """Create marketing strategy"""
        prompt = """Create a 7-day marketing plan for a dropshipping business.
Include:
- Social media strategy
- Ad budget recommendations
- Content ideas
- Target audience"""
        
        return self.think(prompt)
    
    def decide(self, situation):
        """Make a decision about a situation"""
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

# Global CEO instance
ceo = AICEO()
