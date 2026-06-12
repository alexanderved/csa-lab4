import re
from collections.abc import Iterable
from dataclasses import dataclass

COMMENT_TOKEN = ";"
CALL_START_TOKEN = "("
CALL_END_TOKEN = ")"
STRING_DELIM_TOKEN = '"'


@dataclass
class Token:
    value: str
    line: int

    def is_call_start(self):
        return self.value == CALL_START_TOKEN

    def is_call_end(self):
        return self.value == CALL_END_TOKEN

    def is_string_literal(self):
        return self.value[0] == STRING_DELIM_TOKEN and self.value[-1] == STRING_DELIM_TOKEN

    def is_number_literal(self):
        return bool(re.fullmatch(r"[+-]?[0-9]+", self.value))

    def is_symbol(self):
        return not (
            STRING_DELIM_TOKEN in self.value
            or self.is_call_start()
            or self.is_call_end()
            or self.is_string_literal()
            or self.is_number_literal()
        )

    def is_value_expr(self):
        return self.is_string_literal() or self.is_number_literal() or self.is_symbol()

    def is_invalid(self):
        return not (
            self.is_call_start()
            or self.is_call_end()
            or self.is_string_literal()
            or self.is_number_literal()
            or self.is_symbol()
        )


def is_blank(s: str):
    return not s.strip()


def is_terminating(c: str):
    return is_blank(c) or c == CALL_START_TOKEN or c == CALL_END_TOKEN or c == STRING_DELIM_TOKEN


def tokenize(f: Iterable[str]):
    tokens = []

    for i, line in enumerate(f, start=1):
        new_token: Token | None = None
        is_string_literal = False

        for c in line:
            if is_string_literal:
                assert new_token is not None

                new_token.value += c
                if c == STRING_DELIM_TOKEN:
                    new_token = None
                    is_string_literal = False
            else:
                if c == COMMENT_TOKEN:
                    break
                if is_blank(c):
                    new_token = None
                    continue

                if c == CALL_START_TOKEN or c == CALL_END_TOKEN:
                    new_token = None
                    tokens.append(Token(c, i))
                elif c == STRING_DELIM_TOKEN:
                    new_token = Token(c, i)
                    tokens.append(new_token)
                    is_string_literal = True
                elif new_token is None:
                    new_token = Token(c, i)
                    tokens.append(new_token)
                else:
                    new_token.value += c

    return tokens
