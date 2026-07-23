---
name: backend-langchain
description: Use when implementing AI/LLM features with LangChain, LangGraph, or building RAG systems. This skill covers chain composition, agent development, prompt engineering, vector stores, and memory management for AI applications.
---

# AI Development with LangChain

## Overview

This skill provides guidance for building AI applications using LangChain and LangGraph, including RAG systems, agents, and conversational AI.

**Announce at start:** "I'm using the backend-langchain skill for this AI/LLM implementation."

## When to Use

Use this skill when:
- Building LLM-powered applications
- Implementing RAG (Retrieval-Augmented Generation)
- Creating AI agents with tool calling
- Designing prompt templates
- Managing conversation memory
- Orchestrating multi-step AI workflows

## Core Workflow

### Step 1: Understand AI Requirements

Before implementing:
1. Identify the AI task type (Q&A, generation, agent, etc.)
2. Determine data sources and retrieval needs
3. Plan the chain/graph architecture
4. Define evaluation criteria

### Step 2: Design Architecture

1. Choose between simple chains vs LangGraph
2. Plan tool/function definitions
3. Design memory strategy
4. Define output structures with Pydantic

### Step 3: Implement Components

Follow this order:
1. **Prompts** - Design and test prompts
2. **Models** - Configure LLM settings
3. **Tools** - Implement function calling
4. **Chains** - Compose with LCEL
5. **Memory** - Add conversation state

### Step 4: Evaluate and Iterate

1. Test with diverse inputs
2. Evaluate output quality
3. Optimize for cost and latency
4. Add guardrails and validation

## Reference Files

| File | Content | When to Use |
|------|---------|-------------|
| `references/chain-patterns.md` | LCEL, chain composition | Building chains |
| `references/agent-patterns.md` | LangGraph, tool calling | Agent development |
| `references/rag-patterns.md` | Retrieval, embeddings | RAG systems |
| `references/prompt-engineering.md` | Prompt templates, few-shot | Prompt design |
| `references/memory-patterns.md` | Conversation state | Chat applications |

## Key Principles

### Chain Design
- Keep chains composable and testable
- Use LCEL for readable pipelines
- Implement proper error handling
- Add streaming for better UX

### Prompt Engineering
- Be explicit and specific
- Use structured outputs
- Test edge cases
- Version control prompts

### RAG Systems
- Chunk documents appropriately
- Use hybrid search when needed
- Implement re-ranking
- Handle context limits

### Agents
- Define clear tool boundaries
- Implement proper stopping conditions
- Add human-in-the-loop when needed
- Monitor and log agent actions

## Integration with Fairmind

When working on Fairmind tasks:
1. Use `fairmind-context` skill for requirements
2. Check work package for AI specifications
3. Reference architectural blueprints
4. Document model choices in journal

## Example Usage

```python
# Example: RAG-powered Q&A chain
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

# Components
llm = ChatOpenAI(model="gpt-4o", temperature=0)
embeddings = OpenAIEmbeddings()
vectorstore = get_vectorstore(embeddings)
retriever = vectorstore.as_retriever(k=4)

# Prompt
prompt = ChatPromptTemplate.from_template("""
Answer based on the context below.

Context: {context}

Question: {question}
""")

# Chain with LCEL
chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)

# Usage
answer = await chain.ainvoke("What is our refund policy?")
```

## Next Steps

After completing implementation:
- Use `ai-ml-systems` skill for optimization
- Test with diverse queries
- Document prompt templates
