from src.common import WORD_SIZE, Symbol
from src.translator.ast import (
    AST,
    VAR_MANGLE_PREFIX,
    Declare,
    Expr,
    FuncCall,
    FuncCallFlags,
    FuncDef,
    FuncDefFlags,
    IfExpr,
    LetBlock,
    MemRead,
    MemWrite,
    NotAValueError,
    SpecialForm,
    Value,
    VarDef,
    VarDefFlags,
    VarSet,
    unwrap_value,
)
from src.translator.utils import CMP_OPERATORS, Env, EnvFactory, ParseError


class AnalyzerContext:
    functions: dict[Symbol, FuncDef]
    func_calls: dict[Symbol, list[FuncCall]]
    global_vars: dict[Symbol, VarDef]

    env: Env

    def __init__(self):
        self.functions = {}
        self.func_calls = {}
        self.global_vars = {}
        self.env = None

    def collect_functions(self, ast: AST):
        self.functions = {expr.name: expr for expr in ast.expressions if type(expr) is FuncDef}

    def collect_func_calls(self, expr: Expr):
        if type(expr) is FuncCall and expr.name in self.functions:
            if expr.name not in self.func_calls:
                self.func_calls[expr.name] = []
            self.func_calls[expr.name].append(expr)

        for child in expr.children():
            self.collect_func_calls(child)

    def collect_global_vars(self, ast: AST):
        self.global_vars = {expr.name: expr for expr in ast.expressions if type(expr) is VarDef}

    def create_global_env(self):
        self.env = Env(list(self.global_vars.keys()))

    def push_env(self, expr: EnvFactory):
        self.env = expr.create_env(self.env)

    def pop_env(self):
        self.env = self.env.prev


def apply_declare(ctx: AnalyzerContext):
    for func_def in ctx.functions.values():
        for i, expr in enumerate(func_def.body):
            if type(expr) is not Declare:
                break

            if (
                type(expr.property) is not FuncCall
                or len(expr.property.args) != 1
                or type(expr.property.args[0]) is not Value
            ):
                raise ParseError(expr.line, "Объявлено неизвестное свойство")

            if expr.property.name == Symbol("optimize") and expr.property.args[0].value == Symbol(
                VAR_MANGLE_PREFIX + "vector"
            ):
                func_def.flags |= FuncDefFlags.ALLOW_VECTOR
            elif expr.property.name == Symbol("interrupt") and type(expr.property.args[0].value) is int:
                if len(func_def.params) != 0:
                    raise ParseError(func_def.line, "Прерывание не может принимать аргументы")

                func_def.flags |= FuncDefFlags.INTERRUPT
                func_def.int_io_idx = expr.property.args[0].value
            else:
                raise ParseError(expr.line, "Объявлено неизвестное свойство")

        func_def.body = func_def.body[i:]


def mark_recursive_calls(ctx: AnalyzerContext):
    def mark_recursive_calls_in_func(func_name: Symbol, expr: Expr):
        if type(expr) is FuncCall and expr.name == func_name:
            expr.flags |= FuncCallFlags.RECURSIVE

        for child in expr.children():
            mark_recursive_calls_in_func(func_name, child)

    for func_name, func_def in ctx.functions.items():
        mark_recursive_calls_in_func(func_name, func_def)


def mark_tail_calls(ctx: AnalyzerContext):
    def mark_tail_calls_in_expr(expr: Expr, ctx: AnalyzerContext):
        match expr:
            case FuncCall(name=name) if name in ctx.functions:
                expr.flags |= FuncCallFlags.TAIL if FuncCallFlags.RECURSIVE in expr.flags else FuncCallFlags.NONE

            case FuncDef(body=[*_, tail]):
                mark_tail_calls_in_expr(tail, ctx)

            case LetBlock(body=[*_, tail]):
                mark_tail_calls_in_expr(tail, ctx)

            case IfExpr(if_branch=if_branch, else_branch=else_branch):
                mark_tail_calls_in_expr(if_branch, ctx)
                mark_tail_calls_in_expr(else_branch, ctx)

    for func_def in ctx.functions.values():
        mark_tail_calls_in_expr(func_def, ctx)


def mark_inline_calls(ctx: AnalyzerContext):
    for func_name, calls in ctx.func_calls.items():
        if FuncDefFlags.INTERRUPT in ctx.functions[func_name].flags:
            continue

        tail_calls = []
        non_recursive_calls = []
        for c in calls:
            if FuncCallFlags.TAIL in c.flags:
                tail_calls.append(c)

            if FuncCallFlags.RECURSIVE not in c.flags:
                non_recursive_calls.append(c)

        if len(calls) == len(tail_calls) + len(non_recursive_calls) and len(non_recursive_calls) == 1:
            ctx.functions[func_name].flags |= FuncDefFlags.INLINE
            non_recursive_calls[0].flags |= FuncCallFlags.INLINE


def mark_consts(ast: AST, ctx: AnalyzerContext):
    def mark_global_var_write(expr: Expr, ctx: AnalyzerContext):
        match expr:
            case VarSet(name=name, value=value) if ctx.env.is_var_global(name):
                ctx.global_vars[name].flags &= ~VarDefFlags.CONST
                mark_global_var_write(value, ctx)

            case LetBlock(vars=variables, body=body):
                for var_init in variables.values():
                    mark_global_var_write(var_init, ctx)

                ctx.push_env(expr)
                for body_expr in body:
                    mark_global_var_write(body_expr, ctx)
                ctx.pop_env()

            case FuncDef(body=body):
                ctx.push_env(expr)
                for body_expr in body:
                    mark_global_var_write(body_expr, ctx)
                ctx.pop_env()

            case _:
                for child in expr.children():
                    mark_global_var_write(child, ctx)

    for var_def in ctx.global_vars.values():
        var_def.flags |= VarDefFlags.CONST

    mark_global_var_write(ast, ctx)


class VectorContext:
    func: FuncDef
    ind_var: Symbol
    inside_if: bool

    def __init__(self, func, ind_var):
        self.func = func
        self.ind_var = ind_var
        self.inside_if = False

    def enter_if(self):
        self.inside_if = True

    def leave_if(self):
        self.inside_if = False


def analyze_stop_cond(expr: Expr, ctx: AnalyzerContext):
    if type(expr) is not FuncCall or expr.name.value not in ("<", "<=", ">", ">="):
        return None

    args: list[Value] = []
    for arg in expr.args:
        if type(arg) is not Value or type(arg.value) is str:
            return None
        else:
            args.append(arg)

    ind_vars = [
        (idx, arg.value)
        for idx, arg in enumerate(args)
        if type(arg.value) is Symbol and ctx.env.is_var_func_param(arg.value)
    ]

    if len(ind_vars) != 1:
        return None
    ind_var_idx, ind_var = ind_vars[0]

    bound_value = args[1 - ind_var_idx].value
    if type(bound_value) is Symbol and not (
        ctx.env.is_var_global(bound_value) and VarDefFlags.CONST in ctx.global_vars[bound_value].flags
    ):
        return None
    if type(bound_value) is str:
        return None

    cmp_op = expr.name.value
    use_if_branch = (cmp_op.startswith("<") and ind_var_idx == 0) or (cmp_op.startswith(">") and ind_var_idx == 1)

    return (use_if_branch, ind_var)


def is_vectorizable_func_call(fc: FuncCall, vec_ctx: VectorContext, ctx: AnalyzerContext):
    if fc.name == vec_ctx.func.name:
        if FuncCallFlags.TAIL not in fc.flags:
            return False

        for idx, param in enumerate(vec_ctx.func.params):
            if not ctx.env.is_var_func_param(param):
                return False

            if param == vec_ctx.ind_var:
                ind_var_inc = fc.args[idx]
                if type(ind_var_inc) is not FuncCall or ind_var_inc.name.value != "+":
                    return False

                arg0 = unwrap_value(ind_var_inc.args[0])
                arg1 = unwrap_value(ind_var_inc.args[1])
                if not (arg0 == vec_ctx.ind_var and arg1 == 1) and not (arg0 == 1 and arg1 == vec_ctx.ind_var):
                    return False
            else:
                arg = unwrap_value(fc.args[idx])
                if arg != param:
                    return False

        return True
    else:
        res = fc.name.value in {"+", "-", "*", "/"}
        for arg in fc.args:
            res = res and is_vectorizable_expr(arg, vec_ctx, ctx)

        return res


def is_vectorizable_cond(expr: Expr, vec_ctx: VectorContext, ctx: AnalyzerContext):
    if type(expr) is FuncCall:
        if expr.name.value in CMP_OPERATORS:
            res = True
            for arg in expr.args:
                res = res and is_vectorizable_expr(arg, vec_ctx, ctx)

            return res

    return is_vectorizable_expr(expr, vec_ctx, ctx)


def is_vectorizable_special_form(expr: SpecialForm, vec_ctx: VectorContext, ctx: AnalyzerContext):
    res = True
    match expr:
        case MemRead(step_size=step_size) | MemWrite(step_size=step_size) if step_size == WORD_SIZE:
            res = ctx.env.is_var_global(expr.ptr) and unwrap_value(expr.offset) == vec_ctx.ind_var

        case VarSet(name=name) if ctx.env.is_var_local(name):
            res = is_vectorizable_expr(expr.value, vec_ctx, ctx)

        case LetBlock():
            for val in expr.vars.values():
                res = res and is_vectorizable_expr(val, vec_ctx, ctx)

            ctx.push_env(expr)
            for body_expr in expr.body:
                res = res and is_vectorizable_expr(body_expr, vec_ctx, ctx)
            ctx.pop_env()
        case IfExpr() if not vec_ctx.inside_if:
            vec_ctx.enter_if()

            res = is_vectorizable_cond(expr.cond, vec_ctx, ctx)
            res = res and is_vectorizable_expr(expr.if_branch, vec_ctx, ctx)
            res = res and is_vectorizable_expr(expr.else_branch, vec_ctx, ctx)

            vec_ctx.leave_if()
        case _:
            res = False

    return res


def is_vectorizable_expr(expr: Expr, vec_ctx: VectorContext, ctx: AnalyzerContext):
    match expr:
        case Value():
            return type(expr.value) is int or (type(expr.value) is Symbol and ctx.env.is_var_local(expr.value))

        case FuncCall():
            return is_vectorizable_func_call(expr, vec_ctx, ctx)

        case SpecialForm():
            return is_vectorizable_special_form(expr, vec_ctx, ctx)

        case _:
            return False


def find_tail_exprs(expr: Expr, tail_exprs: list[Expr], ctx: AnalyzerContext):
    match expr:
        case LetBlock(body=[*_, tail]):
            find_tail_exprs(tail, tail_exprs, ctx)

        case IfExpr(if_branch=if_branch, else_branch=else_branch):
            find_tail_exprs(if_branch, tail_exprs, ctx)
            find_tail_exprs(else_branch, tail_exprs, ctx)

        case _:
            tail_exprs.append(expr)


def is_vectorizable_func(func: FuncDef, ctx: AnalyzerContext):
    if len(func.body) != 1 or FuncDefFlags.ALLOW_VECTOR not in func.flags:
        return False
    body_expr = func.body[0]

    if type(body_expr) is not IfExpr:
        return False

    ctx.push_env(func)

    stop_cond = analyze_stop_cond(body_expr.cond, ctx)
    if stop_cond is None:
        ctx.pop_env()
        return False

    use_if_branch, ind_var = stop_cond
    main_loop = body_expr.if_branch if use_if_branch else body_expr.else_branch

    vec_ctx = VectorContext(func, ind_var)
    res = is_vectorizable_expr(main_loop, vec_ctx, ctx)

    tail_exprs: list[Expr] = []
    find_tail_exprs(main_loop, tail_exprs, ctx)
    res = res and all(map(lambda e: type(e) is FuncCall and e.name == func.name, tail_exprs))

    ctx.pop_env()

    return res


def mark_vector_funcs(ctx: AnalyzerContext):
    for func in ctx.functions.values():
        try:
            if is_vectorizable_func(func, ctx):
                func.flags |= FuncDefFlags.VECTOR
        except NotAValueError:
            pass


def analyze(ast: AST):
    ctx = AnalyzerContext()

    ctx.collect_functions(ast)
    apply_declare(ctx)
    mark_recursive_calls(ctx)
    mark_tail_calls(ctx)

    ctx.collect_func_calls(ast)
    mark_inline_calls(ctx)

    ctx.collect_global_vars(ast)
    ctx.create_global_env()
    mark_consts(ast, ctx)

    mark_vector_funcs(ctx)
