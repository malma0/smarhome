"""Quick interactive REPL for testing the Jarvis agent without HTTP or voice.

Usage: python chat_cli.py
"""

import asyncio
import uuid

from app.agent import agent


async def main():
    session_id = str(uuid.uuid4())
    print("Jarvis CLI - type 'quit' to exit.\n")
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not message:
            continue
        if message.lower() in {"quit", "exit"}:
            break
        result = await agent.chat(session_id, message)
        print(f"jarvis> {result['response']}")
        for action in result["actions"]:
            print(f"   [action] {action['tool']}({action['input']}) -> {action['result']}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
