"""Input rules for accounts: phone numbers and password strength.

The browser shows the same rules as a live checklist, but the server is the authority.
"""

import re

import phonenumbers

DEFAULT_REGION = "IN"  # numbers typed without +country code are Indian
PASSWORD_MIN, PASSWORD_MAX = 10, 128
TERMS_VERSION = "2026-10-draft"

# The most common passwords (and patterns) that pass length rules. Not exhaustive: the
# real defence is the length + mixed-character rule and login rate limiting.
_COMMON = {
    "password123", "password1234", "qwerty12345", "qwertyuiop1", "1234567890a",
    "a1234567890", "iloveyou123", "welcome1234", "admin123456", "india123456",
    "letmein12345", "passw0rd123", "abc123456789", "monkey123456", "football123",
    "nifty50nifty", "sensex12345", "trading12345", "stockmarket1",
}  # fmt: skip


class ValidationError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Return the number in E.164 form (+919876543210), or raise ValidationError."""
    value = (raw or "").strip()
    if not value:
        raise ValidationError("Enter your phone number.")
    if len(value) > 32 or re.search(r"[^\d\s()+.\-]", value):
        raise ValidationError("Use digits only, e.g. 98765 43210 or +44 20 7946 0958.")
    try:
        number = phonenumbers.parse(value, DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        raise ValidationError("That doesn't look like a phone number.") from None
    if not phonenumbers.is_valid_number(number):
        raise ValidationError("That phone number isn't valid. Check the digits and country code.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def display_phone(e164: str | None) -> str | None:
    """+919876543210 -> +91 98765 43210 (for showing back to the user)."""
    if not e164:
        return None
    try:
        return phonenumbers.format_number(
            phonenumbers.parse(e164, None), phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    except phonenumbers.NumberParseException:
        return e164


def password_problems(password: str, *, email: str = "", name: str = "") -> list[str]:
    """Everything wrong with a password, as messages a person can act on."""
    problems = []
    if len(password) < PASSWORD_MIN:
        problems.append(f"Use at least {PASSWORD_MIN} characters.")
    if len(password) > PASSWORD_MAX:
        problems.append(f"Use at most {PASSWORD_MAX} characters.")
    if not re.search(r"[A-Za-z]", password):
        problems.append("Include at least one letter.")
    if not re.search(r"\d", password):
        problems.append("Include at least one number.")
    lowered = password.lower()
    if lowered in _COMMON or len(set(lowered)) <= 3:
        problems.append("This password is too common or repetitive.")
    personal = [email.split("@")[0].lower()] + [p.lower() for p in name.split()]
    if any(len(part) >= 4 and part in lowered for part in personal):
        problems.append("Don't use your name or email in your password.")
    return problems


def clean_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "").strip())
    if len(name) < 2:
        raise ValidationError("Enter your name.")
    if len(name) > 120:
        raise ValidationError("That name is too long.")
    if re.search(r"[<>{}\[\]\\/@]|https?:", name, re.IGNORECASE):
        raise ValidationError("Names can't contain links or special characters.")
    return name
