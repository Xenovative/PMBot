import secrets

if __name__ == "__main__":
    # 64 hex chars (256-bit key)
    secret = secrets.token_hex(32)
    print(secret)
