"""Phase 7: redaction precision and recall on fixtures of secrets, PII and look-alikes (all values are fake)."""
from recall.sync.redact import PLACEHOLDER, Vault, redact

SECRETS = [  # (text, the value that must disappear)
    ("export OPENAI_KEY=sk-proj-4fQ9zX2LmN7pR1tV8wY3bC6dE0gH5jK", "sk-proj-4fQ9zX2LmN7pR1tV8wY3bC6dE0gH5jK"),
    ("token ghp_aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5", "ghp_aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5"),
    ("aws_access_key_id = AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("SLACK=xoxb-123456789012-abcdefghijKL", "xoxb-123456789012-abcdefghijKL"),
    ("GROQ_API_KEY=gsk_Zx8Qw2Er4Ty6Ui8Op0As2Df4Gh6Jk8Lz0Xc2Vb4Nm6Qw8Er", "gsk_Zx8Qw2Er4Ty6Ui8Op0As2Df4Gh6Jk8Lz0Xc2Vb4Nm6Qw8Er"),
    ("key: sb_secret_Ab12Cd34Ef56Gh78Ij90Kl", "sb_secret_Ab12Cd34Ef56Gh78Ij90Kl"),
    ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    ("password = hunter2hunter", "hunter2hunter"),
    ('"api_key": "Q7w9E2r4T6y8U1i3"', "Q7w9E2r4T6y8U1i3"),
    ("my wifi pwd: correcthorse", "correcthorse"),
    ("DB_URL has 9fK2mQ7xL4pZ8vB3nR6tW1yC5hJ0sD9gA2eU in it", "9fK2mQ7xL4pZ8vB3nR6tW1yC5hJ0sD9gA2eU"),
    ("write to sarah.lee@contoso.com by Friday", "sarah.lee@contoso.com"),
    ("Dev (dev+recall@mail.example.org) joined", "dev+recall@mail.example.org"),
    ("call me on +1 415 555 0132 after 5", "+1 415 555 0132"),
    ("office (020) 7946 0958", "(020) 7946 0958"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA7x\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA7x"),
]
KEEP = [  # text that must come through unchanged
    "def test_typed_text_is_searchable_within_5s(tmp_path):",
    "C:/Users/amils/OneDrive/Desktop/recall/recall/memory/activity.py",
    "Meeting on 2026-09-26 at 14:05:33, room 4.12, build 10.0.26200",
    "commit d029343 Plan: Groq free tier for understanding, gte-small for embeddings",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "Sarah: the vendor shortlist is Acme, Globex and Initech, we decide by Friday.",
    "pip install sentence-transformers==3.1.0 fastembed onnxruntime",
    "ENABLE_THE_EXPERIMENTAL_FEATURE_FLAG_FOR_TESTING=1",
    "the password field is skipped by UIA",
    "sha256 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "RecallSearchWindowControllerFactoryImplementation",
    "IP 192.168.1.20 port 8765, 1,000 requests per day",
]
MIN_PRECISION, MIN_RECALL = 0.95, 0.95


def test_redaction_precision_and_recall():
    vault = Vault()
    caught = [value not in redact(text, vault) for text, value in SECRETS]
    wrongly = [redact(text, vault) != text for text in KEEP]
    recall = sum(caught) / len(caught)
    precision = sum(caught) / (sum(caught) + sum(wrongly))
    print(f"redaction precision {precision:.2f}, recall {recall:.2f}")
    missed = [text for (text, _), ok in zip(SECRETS, caught) if not ok]
    false = [text for text, bad in zip(KEEP, wrongly) if bad]
    assert recall >= MIN_RECALL, f"missed: {missed}"
    assert precision >= MIN_PRECISION, f"redacted by mistake: {false}"


def test_placeholders_are_stable_and_restore_only_through_the_vault():
    vault = Vault()
    once = redact("mail sarah.lee@contoso.com and dev@contoso.com", vault)
    again = redact("reply to sarah.lee@contoso.com", vault)
    assert once == "mail ⟨EMAIL:1⟩ and ⟨EMAIL:2⟩" and again == "reply to ⟨EMAIL:1⟩"
    assert vault.restore(once) == "mail sarah.lee@contoso.com and dev@contoso.com"
    assert Vault().restore(once) == once  # another device's vault can't restore it


def test_only_the_value_of_an_assignment_is_hidden():
    assert redact("password = hunter2hunter;", Vault()) == "password = ⟨SECRET:1⟩;"
    assert PLACEHOLDER.fullmatch(redact("sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx", Vault()))
