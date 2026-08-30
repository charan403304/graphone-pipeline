"""
Mock canonical entity database (Phase IV requirement: "you may mock a small
database of 50 known AI startups"). In production this seed set is the bootstrap
for a `canonical_entities` table that grows via the resolver's own
`llm_arbitration` path (see resolver.py) plus a human review queue for anything
under the confidence threshold.

Each canonical entity lists common raw-string aliases actually seen in the wild
(legal suffixes, rebrands, punctuation variants) so exact/alias matching catches
the bulk of cases before we ever pay for a fuzzy match or an LLM call.
"""

from __future__ import annotations

CANONICAL_STARTUPS: dict[str, list[str]] = {
    "OpenAI": ["OpenAI, Inc.", "Open AI", "OpenAI Inc", "OpenAI LP"],
    "Anthropic": ["Anthropic PBC", "Anthropic, PBC", "Anthropic AI"],
    "Google DeepMind": ["DeepMind", "DeepMind Technologies", "Google Deep Mind"],
    "xAI": ["X.AI", "X AI Corp", "xAI Corp"],
    "Mistral AI": ["Mistral", "Mistral AI SAS"],
    "Cohere": ["Cohere Inc", "Cohere AI", "Cohere Technologies"],
    "Perplexity AI": ["Perplexity", "Perplexity.ai"],
    "Stability AI": ["Stability AI Ltd", "Stability.ai"],
    "Inflection AI": ["Inflection", "Inflection AI Inc"],
    "Character.AI": ["Character AI", "CharacterAI", "Character Technologies"],
    "Hugging Face": ["HuggingFace", "Hugging Face Inc"],
    "Scale AI": ["Scale", "Scale AI Inc"],
    "Databricks": ["Databricks Inc", "Data Bricks"],
    "Together AI": ["Together", "Together Computer"],
    "Groq": ["Groq Inc"],
    "Adept AI": ["Adept", "Adept AI Labs"],
    "Runway": ["Runway ML", "Runway AI Inc", "RunwayML"],
    "ElevenLabs": ["Eleven Labs", "ElevenLabs Inc"],
    "Midjourney": ["Midjourney Inc"],
    "Suno": ["Suno AI", "Suno Inc"],
    "Glean": ["Glean Technologies", "Glean Inc"],
    "Harvey": ["Harvey AI", "Harvey Inc"],
    "Sierra": ["Sierra AI", "Sierra Technologies"],
    "Cursor": ["Anysphere", "Cursor AI", "Anysphere Inc"],
    "Replit": ["Replit Inc", "Repl.it"],
    "LangChain": ["LangChain Inc", "LangChain AI"],
    "Pinecone": ["Pinecone Systems"],
    "Weights & Biases": ["Weights and Biases", "W&B", "WandB"],
    "Snorkel AI": ["Snorkel", "Snorkel AI Inc"],
    "Tabnine": ["Tabnine Inc", "Codota"],
    "Synthesia": ["Synthesia Ltd", "Synthesia Inc"],
    "Jasper": ["Jasper AI", "Jasper.ai"],
    "Writer": ["Writer Inc", "Writer.com", "Qordoba"],
    "Contextual AI": ["Contextual", "Contextual AI Inc"],
    "Imbue": ["Imbue Inc", "Generally Intelligent"],
    "Sakana AI": ["Sakana", "Sakana AI Inc"],
    "Reka AI": ["Reka", "Reka AI Inc"],
    "Voyage AI": ["Voyage", "Voyage AI Inc"],
    "LlamaIndex": ["Llama Index", "LlamaIndex Inc"],
    "Modal": ["Modal Labs", "Modal Labs Inc"],
    "Baseten": ["Baseten Labs", "Baseten Inc"],
    "Fireworks AI": ["Fireworks", "Fireworks AI Inc"],
    "Cerebras": ["Cerebras Systems"],
    "SambaNova Systems": ["SambaNova", "SambaNova Systems Inc"],
    "Lambda": ["Lambda Labs", "Lambda Inc"],
    "CoreWeave": ["Core Weave", "CoreWeave Inc"],
    "Vercel": ["Vercel Inc", "ZEIT"],
    "Notion": ["Notion Labs", "Notion Labs Inc"],
    "Descript": ["Descript Inc"],
    "Speak": ["Speak Labs", "Speak AI"],
    "Krea AI": ["Krea", "Krea AI Inc"],
}
