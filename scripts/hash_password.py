"""Print a TIMETAGGER_CREDENTIALS line for .env.

Run locally so the password never leaves this machine:
    uv run --with bcrypt python scripts/hash_password.py
"""

import getpass

import bcrypt


def main() -> None:
    username = input("TimeTagger username: ").strip()
    if not username or ":" in username or "," in username:
        raise SystemExit("Username must be non-empty and must not contain ':' or ','.")
    password = getpass.getpass("TimeTagger password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("Passwords do not match.")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    print(f"TIMETAGGER_CREDENTIALS='{username}:{hashed}'")


if __name__ == "__main__":
    main()
