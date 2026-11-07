"""Safe parser and evaluator for per-chat Telegram download filters."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class FilterValidationError(ValueError):
    """Raised when a channel download filter is invalid."""


@dataclass(frozen=True)
class Token:
    kind: str
    value: Any
    position: int


@dataclass(frozen=True)
class StringOperand:
    value: str
    regex: bool = False


FIELD_ALIASES = {
    "message_date": "message_date",
    "message_date_time": "message_date",
    "message_id": "message_id",
    "id": "message_id",
    "message_caption": "message_caption",
    "caption": "message_caption",
    "media_file_size": "media_file_size",
    "file_size": "media_file_size",
    "media_width": "media_width",
    "media_height": "media_height",
    "media_file_name": "media_file_name",
    "file_name": "media_file_name",
    "media_duration": "media_duration",
    "media_type": "media_type",
    "file_extension": "file_extension",
}
FIELD_TYPES = {
    "message_date": "date",
    "message_id": "int",
    "message_caption": "str",
    "media_file_size": "int",
    "media_width": "int",
    "media_height": "int",
    "media_file_name": "str",
    "media_duration": "int",
    "media_type": "str",
    "file_extension": "str",
}
UNIT_MULTIPLIERS = {
    "B": 1,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
}
DATE_RE = re.compile(
    r"\d{4}[./-]\d{1,2}(?:[./-]\d{1,2})?(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
)
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:[KMGT]?B)?", re.IGNORECASE)
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DATE_VALUE_RE = re.compile(
    r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$"
)


def parse_filter_datetime(value: str) -> datetime:
    normalized = value.strip().replace(".", "-").replace("/", "-")
    match = DATE_VALUE_RE.fullmatch(normalized)
    if match is None:
        raise FilterValidationError(f"无效的日期值: {value}")
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(
            int(year),
            int(month),
            int(day or 1),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=UTC,
        )
    except ValueError as exc:
        raise FilterValidationError(f"无效的日期值: {value}") from exc


def _tokenize(expression: str) -> list[Token]:
    tokens: list[Token] = []
    position = 0
    length = len(expression)
    while position < length:
        char = expression[position]
        if char.isspace():
            position += 1
            continue
        if expression.startswith("&&", position):
            tokens.append(Token("AND", "and", position))
            position += 2
            continue
        if expression.startswith("||", position):
            tokens.append(Token("OR", "or", position))
            position += 2
            continue
        if expression.startswith((">=", "<=", "!=", "=="), position):
            tokens.append(Token("OP", expression[position : position + 2], position))
            position += 2
            continue
        if char in "><":
            tokens.append(Token("OP", char, position))
            position += 1
            continue
        if char == "(":
            tokens.append(Token("LPAREN", char, position))
            position += 1
            continue
        if char == ")":
            tokens.append(Token("RPAREN", char, position))
            position += 1
            continue
        if char == "*":
            tokens.append(Token("MULT", char, position))
            position += 1
            continue
        if char == "!":
            tokens.append(Token("NOT", "not", position))
            position += 1
            continue
        if char in ("'", '"') or (
            char in "rR" and position + 1 < length and expression[position + 1] in ("'", '"')
        ):
            raw = char in "rR"
            quote_position = position + 1 if raw else position
            quote = expression[quote_position]
            cursor = quote_position + 1
            escaped = False
            while cursor < length:
                current = expression[cursor]
                if current == quote and not escaped:
                    break
                if current == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                cursor += 1
            if cursor >= length:
                raise FilterValidationError(f"第 {position + 1} 个字符处的字符串未闭合")
            literal = expression[position : cursor + 1]
            try:
                value = ast.literal_eval(literal)
            except (SyntaxError, ValueError) as exc:
                raise FilterValidationError(f"无效的字符串: {literal}") from exc
            tokens.append(Token("REGEX" if raw else "STRING", value, position))
            position = cursor + 1
            continue
        date_match = DATE_RE.match(expression, position)
        if date_match is not None:
            end = date_match.end()
            tokens.append(Token("DATE", date_match.group(0), position))
            position = end
            continue
        number_match = NUMBER_RE.match(expression, position)
        if number_match is not None:
            raw_number = number_match.group(0)
            unit = ""
            number_text = raw_number
            unit_match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGT]?B)?", raw_number, re.IGNORECASE)
            if unit_match:
                number_text, unit = unit_match.groups()
            tokens.append(Token("NUMBER", (number_text, (unit or "").upper()), position))
            position = number_match.end()
            continue
        identifier_match = IDENTIFIER_RE.match(expression, position)
        if identifier_match is not None:
            value = identifier_match.group(0)
            lower = value.lower()
            kind = "AND" if lower == "and" else "OR" if lower == "or" else "NOT" if lower == "not" else "IDENT"
            tokens.append(Token(kind, lower if kind != "IDENT" else value, position))
            position = identifier_match.end()
            continue
        raise FilterValidationError(
            f"无法识别第 {position + 1} 个字符: {expression[position]!r}"
        )
    tokens.append(Token("EOF", "", length))
    return tokens


class _Parser:
    def __init__(self, expression: str):
        self.expression = expression
        self.tokens = _tokenize(expression)
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def parse(self):
        if self.current.kind == "EOF":
            return None
        node = self.parse_or()
        if self.current.kind != "EOF":
            raise FilterValidationError(
                f"第 {self.current.position + 1} 个字符附近存在多余内容"
            )
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.current.kind == "OR":
            self.advance()
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.current.kind == "AND":
            self.advance()
            node = ("and", node, self.parse_not())
        return node

    def parse_not(self):
        if self.current.kind == "NOT":
            self.advance()
            return ("not", self.parse_not())
        return self.parse_primary()

    def parse_primary(self):
        if self.current.kind == "LPAREN":
            self.advance()
            node = self.parse_or()
            if self.current.kind != "RPAREN":
                raise FilterValidationError("缺少右括号")
            self.advance()
            return node
        if self.current.kind != "IDENT":
            raise FilterValidationError(
                f"第 {self.current.position + 1} 个字符处应填写字段名"
            )
        field_token = self.advance()
        field = FIELD_ALIASES.get(field_token.value.lower())
        if field is None:
            raise FilterValidationError(f"不支持的过滤字段: {field_token.value}")
        if self.current.kind != "OP":
            raise FilterValidationError(f"字段 {field_token.value} 后缺少比较运算符")
        operator = self.advance().value
        operand = self.parse_operand(field)
        if field == "message_date" and operator not in {">", "<", ">=", "<=", "!=", "=="}:
            raise FilterValidationError("日期字段不支持该比较运算符")
        if FIELD_TYPES[field] in {"int", "date"} and operator not in {">", "<", ">=", "<=", "!=", "=="}:
            raise FilterValidationError("数值和日期字段不支持该比较运算符")
        if FIELD_TYPES[field] == "str" and operator not in {"!=", "=="}:
            raise FilterValidationError("文本字段只支持 == 和 !=")
        return ("compare", field, operator, operand)

    def parse_operand(self, field: str):
        field_type = FIELD_TYPES[field]
        if field_type == "date":
            if self.current.kind != "DATE":
                raise FilterValidationError(f"字段 {field} 需要日期值")
            return parse_filter_datetime(self.advance().value)
        if field_type == "int":
            if self.current.kind != "NUMBER":
                raise FilterValidationError(f"字段 {field} 需要数字值")
            value = self._number_value(self.advance())
            while self.current.kind == "MULT":
                self.advance()
                if self.current.kind != "NUMBER":
                    raise FilterValidationError("乘号后缺少数字")
                value *= self._number_value(self.advance())
            return int(value)
        if self.current.kind not in {"STRING", "REGEX"}:
            raise FilterValidationError(f"字段 {field} 需要带引号的文本值")
        token = self.advance()
        if token.kind == "REGEX":
            try:
                re.compile(str(token.value))
            except re.error as exc:
                raise FilterValidationError(f"无效的正则表达式: {exc}") from exc
        return StringOperand(str(token.value), token.kind == "REGEX")

    @staticmethod
    def _number_value(token: Token) -> float:
        number_text, unit = token.value
        try:
            return float(number_text) * UNIT_MULTIPLIERS.get(unit, 1)
        except ValueError as exc:
            raise FilterValidationError("无效的数字") from exc


class DownloadFilter:
    def __init__(self, expression: str):
        self.expression = expression.strip()
        self.ast = _Parser(self.expression).parse() if self.expression else None

    @property
    def is_empty(self) -> bool:
        return self.ast is None

    def matches(self, context: dict[str, Any]) -> bool:
        return True if self.ast is None else bool(self._evaluate(self.ast, context))

    @classmethod
    def _evaluate(cls, node, context: dict[str, Any]) -> bool:
        kind = node[0]
        if kind == "and":
            return cls._evaluate(node[1], context) and cls._evaluate(node[2], context)
        if kind == "or":
            return cls._evaluate(node[1], context) or cls._evaluate(node[2], context)
        if kind == "not":
            return not cls._evaluate(node[1], context)
        _, field, operator, operand = node
        actual = context.get(field)
        if operator in {"==", "!="} and isinstance(operand, StringOperand):
            text = "" if actual is None else str(actual)
            if operand.regex:
                matched = re.fullmatch(operand.value, text) is not None
            else:
                matched = text == operand.value
            return matched if operator == "==" else not matched
        if isinstance(operand, datetime):
            if actual is None:
                return False
            if not isinstance(actual, datetime):
                actual = parse_filter_datetime(str(actual))
            elif actual.tzinfo is None:
                actual = actual.replace(tzinfo=UTC)
            else:
                actual = actual.astimezone(UTC)
            left, right = actual, operand
        else:
            left = int(actual or 0)
            right = int(operand)
        if operator == "==":
            return left == right
        if operator == "!=":
            return left != right
        if operator == ">":
            return left > right
        if operator == "<":
            return left < right
        if operator == ">=":
            return left >= right
        if operator == "<=":
            return left <= right
        return False


def compile_download_filter(expression: str | None) -> DownloadFilter:
    try:
        return DownloadFilter(expression or "")
    except FilterValidationError:
        raise
    except Exception as exc:
        raise FilterValidationError(f"规则语法错误: {exc}") from exc


def validate_download_filter(expression: str | None) -> None:
    compile_download_filter(expression)


def build_filter_context(
    message: Any,
    media_type: str,
    media: Any,
    file_name: str | None,
    extension: str,
) -> dict[str, Any]:
    return {
        "message_date": getattr(message, "date", None),
        "message_id": int(getattr(message, "id", 0) or 0),
        "message_caption": getattr(message, "caption", None)
        or getattr(message, "text", None)
        or "",
        "media_file_size": int(getattr(media, "file_size", 0) or 0),
        "media_width": int(getattr(media, "width", 0) or 0),
        "media_height": int(getattr(media, "height", 0) or 0),
        "media_file_name": file_name or "",
        "media_duration": int(getattr(media, "duration", 0) or 0),
        "media_type": media_type,
        "file_extension": extension.lower().lstrip("."),
    }
