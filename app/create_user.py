import argparse
import hashlib
import json
import secrets

from app.db import open_db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    args = parser.parse_args()

    api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    with open_db() as conn:
        row = conn.execute(
            """
            INSERT INTO users (email, api_key_hash)
            VALUES (%s, %s)
            ON CONFLICT (email)
            DO UPDATE SET api_key_hash = EXCLUDED.api_key_hash
            RETURNING id
            """,
            (args.email, api_key_hash),
        ).fetchone()

    print(json.dumps({"user_id": str(row[0]), "api_key": api_key}))


if __name__ == "__main__":
    main()
