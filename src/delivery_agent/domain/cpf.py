"""Brazilian CPF helpers: normalization, check-digit validation and masking for logs."""

import re

_NON_DIGITS = re.compile(r"\D")
_ALLOWED = re.compile(r"^[\d.\-\s]+$")


def normalize_cpf(value: str) -> str:
    """Strip every non-digit character (dots, dashes, spaces)."""
    return _NON_DIGITS.sub("", value or "")


def _check_digit(digits: str) -> int:
    """Compute the next CPF check digit (mod 11) for the given digits."""
    weight = len(digits) + 1
    total = sum(int(d) * (weight - i) for i, d in enumerate(digits))
    remainder = (total * 10) % 11
    return 0 if remainder == 10 else remainder


def is_valid_cpf(value: str) -> bool:
    """Validate allowed characters, length, repeated digits and both check digits."""
    if not value or not _ALLOWED.match(value):
        return False
    cpf = normalize_cpf(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    return _check_digit(cpf[:9]) == int(cpf[9]) and _check_digit(cpf[:10]) == int(cpf[10])


def mask_cpf(value: str) -> str:
    """Mask a CPF for logging, keeping only the last 4 digits: `***.***.*89-01`."""
    cpf = normalize_cpf(value)
    if len(cpf) != 11:
        return "***"
    return f"***.***.*{cpf[7:9]}-{cpf[9:]}"
