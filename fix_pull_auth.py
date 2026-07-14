path = "/home/shakeyshep/garmin_pull.py"
with open(path) as f:
    content = f.read()

old = '''print("\\nGarmin Connect Data Puller")
print("-" * 35)
email = input("Garmin email: ").strip()
password = getpass.getpass("Garmin password (hidden): ")

print("\\nConnecting to Garmin Connect...")
try:
    client = Garmin(email, password)
    client.login()
    print("Logged in\\n")
except GarminConnectAuthenticationError:
    print("Login failed - check email/password")
    exit(1)
except Exception as e:
    print(f"Connection error: {e}")
    exit(1)'''

new = '''print("\\nGarmin Connect Data Puller")
print("-" * 35)
TOKENSTORE = "/home/shakeyshep/.garmin_tokens"

print("\\nConnecting to Garmin Connect...")
try:
    client = Garmin(email="", password="")
    client.login(tokenstore=TOKENSTORE)
    print("Logged in\\n")
except GarminConnectAuthenticationError:
    print("Login failed - saved tokens invalid or expired, re-run manual login")
    exit(1)
except Exception as e:
    print(f"Connection error: {e}")
    exit(1)'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(content)
    print("Patched successfully")
else:
    print("Exact match not found - need to check whitespace/emoji characters")
