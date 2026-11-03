#!/usr/bin/env python3
"""
Claude Code prompt: Read current directory files → generate README.md
Backend: Ollama + qwen3.6:27b
"""

import os
import urllib.request
import json

# ── Config ───────────────────────────────────────────────────────────────────

MODEL = "qwen3.6:27b"
OLLAMA_URL = "http://localhost:11434/api/generate"
IGNORE = {".git", "node_modules", "__pycache__", ".env", "venv", ".venv", "dist", "build"}

# ── Read directory ────────────────────────────────────────────────────────────

def read_directory(root="."):
    files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE]
        for fname in filenames:
            if fname == "README.md":
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, root)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(3000)  # cap per file
                files[rel] = content
            except Exception:
                files[rel] = "[binary or unreadable]"
    return files

# ── Build prompt ──────────────────────────────────────────────────────────────

def build_prompt(files: dict) -> str:
    file_block = ""
    for path, content in files.items():
        file_block += f"\n### {path}\n```\n{content}\n```\n"

    return f"""You are a technical writer. Read the files below and write a clear, well-structured README.md for this project.

The README must include:
- Project name and one-line description
- What it does (overview)
- File structure (list the key files and what each does)
- How to install / set up
- How to use (with example commands if applicable)
- Any notes or requirements

Write only the README.md content. Start with # and nothing else.

---

{file_block}
"""

# ── Call Ollama ───────────────────────────────────────────────────────────────

def call_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": True,
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"🤖 Generating README.md with {MODEL}...\n")
    output = []
    with urllib.request.urlopen(req, timeout=300) as resp:
        for line in resp:
            chunk = json.loads(line.decode())
            token = chunk.get("response", "")
            print(token, end="", flush=True)
            output.append(token)
            if chunk.get("done"):
                break
    print()
    return "".join(output)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("📂 Reading directory...")
    files = read_directory(".")
    print(f"   Found {len(files)} file(s)\n")

    prompt = build_prompt(files)
    readme = call_ollama(prompt)

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(readme.strip())

    print("\n✅ README.md created.")

if __name__ == "__main__":
    main()
