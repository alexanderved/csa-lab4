from __future__ import annotations

from src.common import VECTOR_SIZE, WORD_SIZE, Symbol


class ParseError(Exception):
    line: int | None
    msg: str

    def __init__(self, line, msg):
        self.line = line
        self.msg = msg

    def __str__(self):
        return f"Line {self.line}: {self.msg}"


class UnexpectedCallEndError(ParseError):
    def __init__(self, line):
        super().__init__(line, "Неожиданная закрывающая скобка")


SPECIAL_CHARACTERS = {
    "\0": "\\0",
    "\n": "\\n",
    "\t": "\\t",
}


ARITHMETIC_OPERATORS = {"+", "+c", "-", "*", "/", "%"}
BIT_OPERATORS = {"<<", ">>", "&", "|", "^", "~"}
CMP_OPERATORS = {"=", "/=", "<", "<=", ">", ">="}

BUILDIN_SPECIAL_FORMS = {
    "defvar",
    "defun",
    "let",
    "if",
    "setq",
    "allocate-string",
    "allocate-array",
    "read-char",
    "write-char",
    "read-array-element",
    "write-array-element",
    "input",
    "output",
}


class Env:
    vars: list[Symbol]
    nb_allocs: int

    is_vector: bool
    func: Symbol | None
    prev: Env | None

    def __init__(self, variables, nb_allocs=0, is_vector=False, func=None, prev=None):
        self.vars = variables
        self.nb_allocs = nb_allocs

        self.is_vector = is_vector
        self.func = func
        self.prev = prev

    def find_env_with(self, var: Symbol):
        if var in self.vars:
            return self

        if self.prev is None:
            return None

        return self.prev.find_env_with(var)

    def find_func_env(self):
        if self.is_function():
            return self

        if self.prev is None:
            return None

        return self.prev.find_func_env()

    def is_global(self):
        return self.prev is None

    def is_function(self):
        return self.func is not None

    def is_child_of(self, parent_env: Env):
        p = self.prev
        while p is not None and p is not parent_env:
            p = p.prev

        if p is parent_env:
            return True
        else:
            return False

    def is_var_global(self, var: Symbol):
        env = self.find_env_with(var)
        return False if env is None else env.is_global()

    def is_var_func_param(self, var: Symbol):
        env = self.find_env_with(var)
        return False if env is None else env.is_function()

    def is_var_local(self, var: Symbol):
        return not (self.is_var_global(var) or self.is_var_func_param(var))

    def var_sp_offset(self, var: Symbol):
        if self.is_var_global(var):
            raise ValueError("Попытка получить сдвиг глобальной переменной")

        env: Env | None = self
        offset = 0
        while env is not None:
            mul = VECTOR_SIZE if self.is_vector else 1

            if var in env.vars:
                offset += env.vars.index(var) * mul + env.nb_allocs
                break

            offset += len(env.vars) * mul + env.nb_allocs
            env = env.prev
        else:
            raise ValueError(f"Переменная '{var.value}' не найдена")

        return offset * WORD_SIZE

    def param_sp_offset(self, param: Symbol):
        env: Env | None = self
        offset = 0
        while env is not None:
            if env.is_function() and param in env.vars:
                offset += env.vars.index(param) + env.nb_allocs
                break

            mul = VECTOR_SIZE if self.is_vector else 1
            offset += len(env.vars) * mul + env.nb_allocs
            env = env.prev
        else:
            raise ValueError(f"Переменная '{param.value}' не найдена")

        return offset * WORD_SIZE


class EnvFactory:
    def create_env(self, prev=None) -> Env:
        raise NotImplementedError()
