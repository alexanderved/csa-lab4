from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Flag, auto
from typing import Any

from src.common import WORD_SIZE, Symbol
from src.translator.tokenizer import Token
from src.translator.utils import (
    ARITHMETIC_OPERATORS,
    BIT_OPERATORS,
    CMP_OPERATORS,
    Env,
    EnvFactory,
    ParseError,
    UnexpectedCallEndError,
)


@dataclass
class Expr:
    line: int | None

    def children(self):
        return []

    def replace_child(self, orig: Expr, new: Expr):
        pass


@dataclass
class Value(Expr):
    value: int | str | Symbol

    @staticmethod
    def from_token(token: Token):
        if token.is_string_literal():
            str_value = token.value[1:-1]
            str_value = str_value.replace("\\n", "\n")
            str_value = str_value.replace("\\t", "\t")

            return Value(token.line, str_value)
        elif token.is_number_literal():
            return Value(token.line, int(token.value))
        elif token.is_symbol():
            return Value(token.line, Symbol(token.value))
        else:
            raise ParseError(token.line, f"Использование запрещенного символа {token.value} в значении")


class NotAValueError(Exception):
    pass


def unwrap_value(value: Expr):
    if type(value) is not Value:
        raise NotAValueError()

    return value.value


class FuncCallFlags(Flag):
    NONE = 0
    RECURSIVE = auto()
    TAIL = auto()
    INLINE = auto()


@dataclass
class FuncCall(Expr):
    name: Symbol
    args: list[Expr]

    flags: FuncCallFlags = FuncCallFlags.NONE

    def children(self):
        return list(self.args)

    def replace_child(self, orig: Expr, new: Expr):
        for i in range(len(self.args)):
            if self.args[i] is orig:
                self.args[i] = new


@dataclass
class SpecialForm(Expr):
    pass


@dataclass
class MemAlloc(SpecialForm):
    size: int


class VarDefFlags(Flag):
    NONE = 0
    CONST = auto()


@dataclass
class VarDef(SpecialForm):
    name: Symbol
    value: Value | MemAlloc

    flags: VarDefFlags = VarDefFlags.NONE

    def children(self):
        return [self.value]

    def replace_child(self, orig: Expr, new: Expr):
        if self.value is orig:
            assert type(new) is Value | MemAlloc
            self.value = new


class FuncDefFlags(Flag):
    NONE = 0
    INLINE = auto()
    ALLOW_VECTOR = auto()
    VECTOR = auto()
    INTERRUPT = auto()


@dataclass
class FuncDef(SpecialForm, EnvFactory):
    name: Symbol
    params: list[Symbol]
    body: list[Expr]

    flags: FuncDefFlags = FuncDefFlags.NONE
    int_io_idx: int | None = None

    def children(self):
        return list(self.body)

    def replace_child(self, orig: Expr, new: Expr):
        for i in range(len(self.body)):
            if self.body[i] is orig:
                self.body[i] = new

    def create_env(self, prev=None):
        return Env(self.params, nb_allocs=0 if FuncDefFlags.INLINE in self.flags else 1, func=self.name, prev=prev)


@dataclass
class Declare(SpecialForm):
    property: Expr

    def children(self):
        return [self.property]


@dataclass
class VarSet(SpecialForm):
    name: Symbol
    value: Expr

    is_masked: bool = False

    def children(self):
        return [self.value]

    def replace_child(self, orig: Expr, new: Expr):
        if self.value is orig:
            self.value = new


@dataclass
class MemRead(SpecialForm):
    ptr: Symbol
    step_size: int

    offset: Expr

    def children(self):
        return [self.offset]

    def replace_child(self, orig: Expr, new: Expr):
        if self.offset is orig:
            self.offset = new


@dataclass
class MemWrite(SpecialForm):
    ptr: Symbol
    step_size: int

    offset: Expr
    value: Expr

    is_masked: bool = False

    def children(self):
        return [self.offset, self.value]

    def replace_child(self, orig: Expr, new: Expr):
        if self.offset is orig:
            self.offset = new

        if self.value is orig:
            self.value = new


@dataclass
class PortRead(SpecialForm):
    port: int


@dataclass
class PortWrite(SpecialForm):
    port: int
    value: Expr

    def children(self):
        return [self.value]

    def replace_child(self, orig: Expr, new: Expr):
        if self.value is orig:
            self.value = new


@dataclass
class IfExpr(SpecialForm):
    cond: Expr
    if_branch: Expr
    else_branch: Expr

    def children(self):
        return [self.cond, self.if_branch, self.else_branch]

    def replace_child(self, orig: Expr, new: Expr):
        if self.cond is orig:
            self.cond = new

        if self.if_branch is orig:
            self.if_branch = new

        if self.else_branch is orig:
            self.else_branch = new


@dataclass
class LetBlock(SpecialForm, EnvFactory):
    vars: dict[Symbol, Expr]
    body: list[Expr]

    def children(self):
        return list(self.vars.values()) + list(self.body)

    def replace_child(self, orig: Expr, new: Expr):
        for var in self.vars:
            if self.vars[var] is orig:
                self.vars[var] = new

        for i in range(len(self.body)):
            if self.body[i] is orig:
                self.body[i] = new

    def create_env(self, prev=None):
        return Env(list(self.vars.keys()), prev=prev)


class AST(Expr):
    expressions: list[Expr]

    def __init__(self, expressions):
        self.line = 1
        self.expressions = expressions

    def children(self):
        return list(self.expressions)


VAR_MANGLE_PREFIX = "__"


def mangle_var_name(name: str):
    return VAR_MANGLE_PREFIX + name


def parse_defun(defun_line: int, token_iter: Iterator[Token]):
    func_name_token = next(token_iter)
    if not func_name_token.is_symbol():
        raise ParseError(func_name_token.line, "Имя вызываемой функции должно быть символом")
    func_name = Symbol(func_name_token.value)

    next_token = next(token_iter)
    if not next_token.is_call_start():
        raise ParseError(next_token.line, "Отсутствует объявление параметров функции")

    params = []
    next_token = next(token_iter)
    while not next_token.is_call_end():
        if not next_token.is_symbol():
            raise ParseError(func_name_token.line, "Имя параметра должно быть символом")

        param = Symbol(mangle_var_name(next_token.value))
        if param in params:
            raise ParseError(next_token.line, "Повторяющийся параметр функции")
        params.append(param)

        next_token = next(token_iter)

    body = []
    while True:
        try:
            body.append(parse_expr(token_iter))
        except UnexpectedCallEndError:
            break

    return FuncDef(defun_line, func_name, params, body)


def parse_let_block(let_line: int, token_iter: Iterator[Token]):
    next_token = next(token_iter)
    if not next_token.is_call_start():
        raise ParseError(next_token.line, "Отсутствует объявление переменных")

    variables = {}
    next_token = next(token_iter)
    while not next_token.is_call_end():
        if not next_token.is_call_start():
            raise ParseError(next_token.line, "Неизвестная конструкция при объявлении переменных")

        var_name_token = next(token_iter)
        if not var_name_token.is_symbol():
            raise ParseError(next_token.line, "Имя переменной должно быть символом")

        var_name = Symbol(mangle_var_name(var_name_token.value))
        var_value = parse_expr(token_iter)
        variables[var_name] = var_value

        next_token = next(token_iter)
        if not next_token.is_call_end():
            raise ParseError(next_token.line, "Незакрыта скобка")

        next_token = next(token_iter)

    body = []
    while True:
        try:
            body.append(parse_expr(token_iter))
        except UnexpectedCallEndError:
            break

    return LetBlock(let_line, variables, body)


def parse_args(token_iter: Iterator[Token]):
    args = []
    while True:
        try:
            args.append(parse_expr(token_iter))
        except UnexpectedCallEndError:
            break

    return args


ARG_VALIDATORS: dict[str, list[Any]] = {
    "defvar": [
        lambda arg: type(arg) is Value and type(arg.value) is Symbol,
        lambda arg: type(arg) is Value or type(arg) is MemAlloc,
    ],
    "declare": [None],
    "if": [None, None, None],
    "allocate-string": [lambda arg: type(arg) is Value and type(arg.value) is int and arg.value > 0],
    "allocate-array": [lambda arg: type(arg) is Value and type(arg.value) is int and arg.value > 0],
    "setq": [lambda arg: type(arg) is Value and type(arg.value) is Symbol, None],
    "read-char": [lambda arg: type(arg) is Value and type(arg.value) is Symbol, None],
    "write-char": [
        lambda arg: type(arg) is Value and type(arg.value) is Symbol,
        None,
        None,
    ],
    "read-array-element": [lambda arg: type(arg) is Value and type(arg.value) is Symbol, None],
    "write-array-element": [
        lambda arg: type(arg) is Value and type(arg.value) is Symbol,
        None,
        None,
    ],
    "input": [lambda arg: type(arg) is Value and type(arg.value) is int],
    "output": [lambda arg: type(arg) is Value and type(arg.value) is int, None],
}


def validate_args(name_token: Token, args: list[Expr]):
    name = name_token.value
    if name not in ARG_VALIDATORS:
        return

    validators = ARG_VALIDATORS[name]
    if len(args) != len(validators):
        raise ParseError(name_token.line, f"Функции или специальной форме '{name}' передано неверное число аргументов")

    for i, v in enumerate(validators):
        if v is not None and not v(args[i]):
            raise ParseError(args[i].line, f"Неверный формат {i} аргумента функции или специальной форму '{name}'")


def parse_common_call(name_token: Token, token_iter: Iterator[Token]):
    args = parse_args(token_iter)

    if name_token.value == "if" and len(args) == 2:
        args.append(Value(name_token.line, 0))
    validate_args(name_token, args)

    sf_ctors = {
        "declare": Declare,
        "if": IfExpr,
    }

    sf_quoted_arg0_ctors = {
        "defvar": VarDef,
        "setq": VarSet,
        "allocate-string": MemAlloc,
        "input": PortRead,
        "output": PortWrite,
    }

    sf_mem_access_ctors = {
        "read-char": (MemRead, 1),
        "write-char": (MemWrite, 1),
        "read-array-element": (MemRead, WORD_SIZE),
        "write-array-element": (MemWrite, WORD_SIZE),
    }

    match name_token.value:
        case "allocate-array":
            nb_alloc_elements = args[0].value

            return MemAlloc(name_token.line, nb_alloc_elements * WORD_SIZE)

        case name if name in sf_ctors:
            return sf_ctors[name](name_token.line, *args)

        case name if name in sf_quoted_arg0_ctors:
            return sf_quoted_arg0_ctors[name](name_token.line, args[0].value, *args[1:])

        case name if name in sf_mem_access_ctors:
            ctor, step_size = sf_mem_access_ctors[name]

            return ctor(name_token.line, args[0].value, step_size, *args[1:])

        case name:
            if (name in ARITHMETIC_OPERATORS or name in BIT_OPERATORS or name in CMP_OPERATORS) and len(args) != 2:
                raise ParseError(name_token.line, f"Недопустимое CMP_OPERATORS аргументов операции '{name}'")

            return FuncCall(name_token.line, Symbol(name_token.value), args)


def parse_call(token_iter: Iterator[Token]):
    name_token = next(token_iter)
    if not name_token.is_symbol():
        raise ParseError(name_token.line, "Имя вызываемой функции или специальной формы должно быть символом")

    match name_token.value:
        case "defun":
            return parse_defun(name_token.line, token_iter)

        case "let":
            return parse_let_block(name_token.line, token_iter)

    return parse_common_call(name_token, token_iter)


def parse_expr(token_iter: Iterator[Token]) -> Expr:
    token = next(token_iter)

    if token.is_value_expr():
        if token.is_symbol():
            return Value.from_token(Token(mangle_var_name(token.value), token.line))

        return Value.from_token(token)
    elif token.is_call_start():
        try:
            return parse_call(token_iter)
        except StopIteration:
            raise ParseError(token.line, "Незакрытая скобка")
    elif token.is_call_end():
        raise UnexpectedCallEndError(token.line)
    else:
        raise ParseError(token.line, "Неизвестный токен")


def build_ast(tokens: list[Token]):
    def token_iter(tokens: list[Token]):
        for token in tokens:
            if token.is_invalid():
                raise ParseError(token.line, "Невалидный токен")
            yield token

    it = token_iter(tokens)
    expressions = []

    while True:
        try:
            expressions.append(parse_expr(it))
        except StopIteration:
            break

    return AST(expressions)
