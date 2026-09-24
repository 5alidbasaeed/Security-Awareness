_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """
    Spreadsheet apps execute cells starting with = + - @ as formulas. Names and
    departments are admin-entered text that ends up in exports opened in Excel,
    so prefix such cells with an apostrophe (OWASP CSV-injection guidance).
    """
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text
