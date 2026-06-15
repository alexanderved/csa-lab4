import copy
from itertools import chain
from pathlib import Path
from typing import BinaryIO, TextIO

from src.common import (
    ADDRESS_INSTRUCTION_SIZE,
    ADDRESSLESS_INSTRUCTIONS,
    CONTROL_INSTRUCTIONS,
    VECTOR_SIZE,
    WORD_SIZE,
    AddressMode,
    Instruction,
    Opcode,
    Symbol,
    dec_to_hex,
    hex_little_endian,
    to_little_endian,
)
from src.translator.analyzer import analyze
from src.translator.ast import (
    AST,
    Expr,
    FuncCall,
    FuncCallFlags,
    FuncDef,
    FuncDefFlags,
    IfExpr,
    LetBlock,
    MemAlloc,
    MemRead,
    MemWrite,
    PortRead,
    PortWrite,
    SpecialForm,
    Value,
    VarDef,
    VarSet,
    build_ast,
)
from src.translator.tokenizer import tokenize
from src.translator.utils import ARITHMETIC_OPERATORS, BIT_OPERATORS, CMP_OPERATORS, SPECIAL_CHARACTERS, Env, EnvFactory, ParseError


class TranslatorContext:
    string_literals: dict[str, None]
    allocated_memory: int
    global_vars: dict[Symbol, VarDef]

    interrupts: dict[Symbol, FuncDef]
    functions: dict[Symbol, FuncDef]
    inline_functions: dict[Symbol, FuncDef]
    main_program: list[Expr]

    env: Env

    def __init__(self):
        self.string_literals = {}
        self.allocated_memory = 0
        self.global_vars = {}

        self.functions = {}
        self.inline_functions = {}
        self.interrupts = {}
        self.main_program = []

        self.env = Env([])

    def fill_static_memory(self, expr: Expr):
        match expr:
            case Value(value=str(value)):
                self.string_literals[value] = None

            case MemAlloc(size=size):
                self.allocated_memory += size

            case VarDef(name=name):
                if name in self.global_vars:
                    raise ParseError(expr.line, f"Повторное объявление глобальной переменной '{name.value}'")
                self.global_vars[name] = expr

        for child in expr.children():
            self.fill_static_memory(child)

    def collect_code(self, ast: AST):
        for expr in ast.expressions:
            match expr:
                case FuncDef(name=name, flags=flags) if FuncDefFlags.INTERRUPT in flags:
                    self.interrupts[name] = expr

                case FuncDef(name=name, flags=flags) if FuncDefFlags.INLINE in flags:
                    self.inline_functions[name] = expr

                case FuncDef(name=name):
                    self.functions[name] = expr

                case VarDef():
                    continue

                case _:
                    self.main_program.append(expr)

    def create_global_env(self):
        self.env = Env(list(self.global_vars.keys()))

    def push_env(self, expr: EnvFactory):
        self.env = expr.create_env(self.env)

    def pop_env(self):
        if self.env.prev is None:
            self.env = Env([])
        else:
            self.env = self.env.prev


BIN_HEADER_SIZE = ADDRESS_INSTRUCTION_SIZE * 2


class MemoryMap:
    program_start_addr: int | None
    interrupt_vector: int | None

    str_lit_addresses: dict[str, int]

    free_memory_size: int
    free_memory_used: int

    global_vars_addresses: dict[Symbol, int]
    global_var_allocs_addresses: dict[Symbol, int]

    instructions: dict[int, Instruction]
    label_addresses: dict[Symbol, int]

    def __init__(self):
        self.program_start_addr = None
        self.interrupt_vector = None

        self.str_lit_addresses = {}

        self.free_memory_size = 0
        self.free_memory_used = 0

        self.global_vars_addresses = {}
        self.global_var_allocs_addresses = {}

        self.instructions = {}
        self.label_addresses = {}

    def add_string_literal(self, lit: str):
        if lit in self.str_lit_addresses:
            return

        if not self.str_lit_addresses:
            self.str_lit_addresses[lit] = BIN_HEADER_SIZE
        else:
            last_lit = next(reversed(self.str_lit_addresses))
            addr = self.str_lit_addresses[last_lit] + len(last_lit) + 1
            self.str_lit_addresses[lit] = addr

    def string_literals_end_addr(self):
        if len(self.str_lit_addresses) == 0:
            return BIN_HEADER_SIZE

        last_lit = next(reversed(self.str_lit_addresses))
        return self.str_lit_addresses[last_lit] + len(last_lit) + 1

    def add_global_variable(self, var: Symbol, ctx: TranslatorContext):
        if var in self.global_vars_addresses:
            return

        global_var_block_addr = self.string_literals_end_addr() + self.free_memory_size
        self.global_vars_addresses[var] = global_var_block_addr + len(self.global_vars_addresses) * WORD_SIZE

        value = ctx.global_vars[var].value
        if isinstance(value, MemAlloc):
            size = value.size
            self.global_var_allocs_addresses[var] = self.next_free_block_addr(size)

    def map_static_memory(self, ctx: TranslatorContext):
        for str_lit in ctx.string_literals:
            self.add_string_literal(str_lit)

        self.free_memory_size = ctx.allocated_memory

        for global_var in ctx.global_vars:
            self.add_global_variable(global_var, ctx)

    def next_free_block_addr(self, size: int):
        self.free_memory_used += size

        return self.string_literals_end_addr() + self.free_memory_used - size

    def next_instr_addr(self):
        if len(self.instructions) == 0:
            return self.string_literals_end_addr() + self.free_memory_size + len(self.global_vars_addresses) * WORD_SIZE

        last_addr = next(reversed(self.instructions))
        last_size = self.instructions[last_addr].opcode.size()

        return last_addr + last_size

    def generate_instruction(self, instr: Instruction):
        next_addr = self.next_instr_addr()
        self.instructions[next_addr] = instr

        return next_addr

    def generate_push(self, ctx: TranslatorContext):
        self.generate_instruction(Instruction(Opcode.PUSH))
        ctx.env.nb_allocs += 1

    def generate_pop(self, ctx: TranslatorContext):
        self.generate_instruction(Instruction(Opcode.POP))
        ctx.env.nb_allocs -= 1

    def generate_pops(self, nb: int, ctx: TranslatorContext):
        for _ in range(nb):
            self.generate_instruction(Instruction(Opcode.POP))
        ctx.env.nb_allocs -= nb

    def generate_vpush(self, ctx: TranslatorContext):
        self.generate_instruction(Instruction(Opcode.VPUSH))
        ctx.env.nb_allocs += VECTOR_SIZE

    def generate_vpop(self, ctx: TranslatorContext):
        self.generate_instruction(Instruction(Opcode.VPOP))
        ctx.env.nb_allocs -= VECTOR_SIZE

    def generate_vpops(self, nb: int, ctx: TranslatorContext):
        for _ in range(nb):
            self.generate_instruction(Instruction(Opcode.VPOP))
        ctx.env.nb_allocs -= nb * VECTOR_SIZE


def translate_value(value: Value, mm: MemoryMap, ctx: TranslatorContext):
    match value:
        case Value(value=str() as s):
            mm.generate_instruction(Instruction(Opcode.LD, AddressMode.IMM, mm.str_lit_addresses[s]))

        case Value(value=int() as num):
            mm.generate_instruction(Instruction(Opcode.LD, AddressMode.IMM, num))

        case Value(value=Symbol() as sym):
            if ctx.env.is_var_global(sym):
                mm.generate_instruction(Instruction(Opcode.LD, AddressMode.ADDR, sym))
            else:
                mm.generate_instruction(Instruction(Opcode.LD, AddressMode.SP, ctx.env.var_sp_offset(sym)))


def push_mem_access_address(access: MemRead | MemWrite, mm: MemoryMap, ctx: TranslatorContext):
    translate_expr(access.offset, mm, ctx)
    if access.step_size != 1:
        mm.generate_instruction(Instruction(Opcode.MUL, AddressMode.IMM, access.step_size))

    if ctx.env.is_var_global(access.ptr):
        mm.generate_instruction(Instruction(Opcode.ADD, AddressMode.ADDR, access.ptr))
    else:
        mm.generate_instruction(Instruction(Opcode.ADD, AddressMode.SP, ctx.env.var_sp_offset(access.ptr)))

    mm.generate_push(ctx)


def translate_operand(operand: Expr, mm: MemoryMap, ctx: TranslatorContext):
    match operand:
        case Value(value=Symbol() as var):
            if ctx.env.is_var_global(var):
                return AddressMode.ADDR, var, False
            else:
                return AddressMode.SP, ctx.env.var_sp_offset(var), False

        case Value(value=int() as num):
            return AddressMode.IMM, num, False

        case Value(value=str() as s):
            return AddressMode.IMM, mm.str_lit_addresses[s], False

        case MemAlloc(_, size):
            return AddressMode.IMM, mm.next_free_block_addr(size), False

        case MemRead(step_size=step_size) if step_size == WORD_SIZE:
            push_mem_access_address(operand, mm, ctx)

            return AddressMode.SP_IND, 0, True

        case _:
            translate_expr(operand, mm, ctx)
            mm.generate_push(ctx)

            return AddressMode.SP, 0, True


def translate_alu_op(func_call: FuncCall, mm: MemoryMap, ctx: TranslatorContext):
    is_binary = func_call.name.value != "~"

    addr_mode, operand, needs_pop = None, None, False
    if is_binary:
        addr_mode, operand, needs_pop = translate_operand(func_call.args[1], mm, ctx)

    translate_expr(func_call.args[0], mm, ctx)

    alu_op_map = {
        "+": Opcode.ADD,
        "+c": Opcode.ADC,
        "-": Opcode.SUB,
        "*": Opcode.MUL,
        "/": Opcode.DIV,
        "%": Opcode.REM,
        "<<": Opcode.SHL,
        ">>": Opcode.SHR,
        "&": Opcode.AND,
        "|": Opcode.OR,
        "^": Opcode.XOR,
        "~": Opcode.NOT,
    }
    opcode = alu_op_map[func_call.name.value]
    mm.generate_instruction(Instruction(opcode, addr_mode, operand))

    if needs_pop:
        mm.generate_pop(ctx)


def translate_cmp_op(func_call: FuncCall, mm: MemoryMap, ctx: TranslatorContext):
    addr_mode, operand, needs_pop = translate_operand(func_call.args[1], mm, ctx)
    translate_expr(func_call.args[0], mm, ctx)

    mm.generate_instruction(Instruction(Opcode.CMP, addr_mode, operand))

    jmp_op_map = {
        "<": Opcode.JLT,
        "<=": Opcode.JLE,
        ">": Opcode.JGT,
        ">=": Opcode.JGE,
        "=": Opcode.JEQ,
        "/=": Opcode.JNE,
    }
    opcode = jmp_op_map[func_call.name.value]
    alt_branch_jmp = mm.generate_instruction(Instruction(opcode, AddressMode.IMM, None))

    mm.generate_instruction(Instruction(Opcode.LD, AddressMode.IMM, 0))
    main_branch_exit = mm.generate_instruction(Instruction(Opcode.JMP, AddressMode.IMM, None))
    alt_branch_start = mm.generate_instruction(Instruction(Opcode.LD, AddressMode.IMM, 1))

    mm.instructions[alt_branch_jmp].operand = alt_branch_start
    mm.instructions[main_branch_exit].operand = mm.next_instr_addr()

    if needs_pop:
        mm.generate_pop(ctx)


def translate_user_def_func(func_call: FuncCall, mm: MemoryMap, ctx: TranslatorContext):
    if FuncCallFlags.TAIL in func_call.flags:
        func_env = ctx.env.find_func_env()
        assert func_env.func == func_call.name

        for param, arg in zip(reversed(func_env.vars), reversed(func_call.args)):
            translate_expr(arg, mm, ctx)
            mm.generate_instruction(Instruction(Opcode.ST, AddressMode.SP, ctx.env.param_sp_offset(param)))

        clear_env: Env | None = ctx.env
        nb_pops = 0
        while clear_env is not None and clear_env is not func_env:
            nb_pops += len(clear_env.vars)
            clear_env = clear_env.prev

        for _ in range(nb_pops):
            mm.generate_instruction(Instruction(Opcode.POP))

        mm.generate_instruction(Instruction(Opcode.JMP, AddressMode.IMM, func_call.name))
    else:
        for arg in reversed(func_call.args):
            translate_expr(arg, mm, ctx)
            mm.generate_push(ctx)

        if FuncCallFlags.INLINE in func_call.flags:
            func = ctx.inline_functions[func_call.name]
            if FuncDefFlags.VECTOR in func.flags:
                translate_vector_func_def(func, mm, ctx)
            else:
                translate_func_def(func, mm, ctx)
        else:
            mm.generate_instruction(Instruction(Opcode.CALL, AddressMode.IMM, func_call.name))

        mm.generate_pops(len(func_call.args), ctx)


def translate_func_call(func_call: FuncCall, mm: MemoryMap, ctx: TranslatorContext):
    if func_call.name.value in ARITHMETIC_OPERATORS or func_call.name.value in BIT_OPERATORS:
        translate_alu_op(func_call, mm, ctx)
    elif func_call.name.value in CMP_OPERATORS:
        translate_cmp_op(func_call, mm, ctx)
    elif func_call.name in ctx.interrupts and FuncDefFlags.INTERRUPT in ctx.interrupts[func_call.name].flags:
        raise ParseError(func_call.line, "Вызов прерывания из программы запрещен")
    else:
        translate_user_def_func(func_call, mm, ctx)


def translate_let_block(let_block: LetBlock, mm: MemoryMap, ctx: TranslatorContext):
    ctx.env = Env([], prev=ctx.env)
    for var_init in reversed(let_block.vars.values()):
        translate_expr(var_init, mm, ctx)
        mm.generate_push(ctx)
    ctx.pop_env()

    ctx.push_env(let_block)
    for expr in let_block.body:
        translate_expr(expr, mm, ctx)
    ctx.pop_env()

    for _ in range(len(let_block.vars)):
        mm.generate_instruction(Instruction(Opcode.POP))


def translate_if_expr(if_expr: IfExpr, mm: MemoryMap, ctx: TranslatorContext):
    translate_expr(if_expr.cond, mm, ctx)

    mm.generate_instruction(Instruction(Opcode.CMP, AddressMode.IMM, 0))
    else_branch_jmp = mm.generate_instruction(Instruction(Opcode.JEQ, AddressMode.IMM, None))

    translate_expr(if_expr.if_branch, mm, ctx)
    if_branch_exit = mm.generate_instruction(Instruction(Opcode.JMP, AddressMode.IMM, None))

    else_branch_start = mm.next_instr_addr()
    translate_expr(if_expr.else_branch, mm, ctx)

    mm.instructions[else_branch_jmp].operand = else_branch_start
    mm.instructions[if_branch_exit].operand = mm.next_instr_addr()


def translate_other_special_form(sf: SpecialForm, mm: MemoryMap, ctx: TranslatorContext):
    match sf:
        case VarSet(_, name, value):
            translate_expr(value, mm, ctx)

            if ctx.env.is_var_global(name):
                mm.generate_instruction(Instruction(Opcode.ST, AddressMode.ADDR, name))
            else:
                mm.generate_instruction(Instruction(Opcode.ST, AddressMode.SP, ctx.env.var_sp_offset(name)))

        case MemAlloc(_, size):
            mm.generate_instruction(Instruction(Opcode.LD, AddressMode.IMM, mm.next_free_block_addr(size)))

        case MemRead(step_size=step_size) if step_size == WORD_SIZE:
            push_mem_access_address(sf, mm, ctx)
            mm.generate_instruction(Instruction(Opcode.LD, AddressMode.SP_IND, 0))
            mm.generate_pop(ctx)

        case MemRead(step_size=step_size) if step_size == 1:
            push_mem_access_address(sf, mm, ctx)
            mm.generate_instruction(Instruction(Opcode.LD, AddressMode.SP_IND, 0))
            mm.generate_instruction(Instruction(Opcode.AND, AddressMode.IMM, 0xFF))
            mm.generate_pop(ctx)

        case MemWrite(step_size=step_size, value=value) if step_size == WORD_SIZE:
            push_mem_access_address(sf, mm, ctx)

            translate_expr(value, mm, ctx)
            mm.generate_instruction(Instruction(Opcode.ST, AddressMode.SP_IND, 0))

            mm.generate_pop(ctx)

        case MemWrite(step_size=step_size, value=value) if step_size == 1:
            translate_expr(value, mm, ctx)
            mm.generate_instruction(Instruction(Opcode.AND, AddressMode.IMM, 0xFF))
            mm.generate_push(ctx)

            push_mem_access_address(sf, mm, ctx)

            mm.generate_instruction(Instruction(Opcode.LD, AddressMode.SP_IND, 0))
            mm.generate_instruction(Instruction(Opcode.AND, AddressMode.IMM, 0xFFFFFF00))
            mm.generate_instruction(Instruction(Opcode.OR, AddressMode.SP, WORD_SIZE))
            mm.generate_instruction(Instruction(Opcode.ST, AddressMode.SP_IND, 0))
            mm.generate_instruction(Instruction(Opcode.AND, AddressMode.IMM, 0xFF))

            mm.generate_pops(2, ctx)

        case PortRead(_, port):
            mm.generate_instruction(Instruction(Opcode.IN, AddressMode.IMM, port))

        case PortWrite(_, port, value):
            translate_expr(value, mm, ctx)
            mm.generate_instruction(Instruction(Opcode.OUT, AddressMode.IMM, port))

        case _:
            raise ParseError(sf.line, "Неизвестная специальная форма")


def translate_expr(expr: Expr, mm: MemoryMap, ctx: TranslatorContext):
    match expr:
        case Value():
            translate_value(expr, mm, ctx)

        case FuncCall():
            translate_func_call(expr, mm, ctx)

        case LetBlock():
            translate_let_block(expr, mm, ctx)

        case IfExpr():
            translate_if_expr(expr, mm, ctx)

        case SpecialForm():
            translate_other_special_form(expr, mm, ctx)

        case _:
            raise ParseError(expr.line, "Неизвестное выражение")


def translate_func_def(func: FuncDef, mm: MemoryMap, ctx: TranslatorContext):
    ctx.push_env(func)

    mm.label_addresses[func.name] = mm.next_instr_addr()
    for expr in func.body:
        translate_expr(expr, mm, ctx)

    if FuncDefFlags.INTERRUPT in func.flags:
        mm.generate_instruction(Instruction(Opcode.IRET))
    elif FuncDefFlags.INLINE not in func.flags:
        mm.generate_instruction(Instruction(Opcode.RET))

    ctx.pop_env()


def translate_interrupt(intr: FuncDef, mm: MemoryMap, ctx: TranslatorContext):
    if FuncDefFlags.INTERRUPT in intr.flags and intr.int_io_idx == 0 and mm.interrupt_vector is None:
        mm.interrupt_vector = mm.next_instr_addr()

    translate_func_def(intr, mm, ctx)


def extract_vector_info(expr: Expr, ctx: TranslatorContext):
    assert type(expr) is FuncCall

    args: list[Value] = []
    for arg in expr.args:
        assert type(arg) is Value
        args.append(arg)

    ind_vars = [
        (idx, arg.value)
        for idx, arg in enumerate(args)
        if type(arg.value) is Symbol and ctx.env.is_var_func_param(arg.value)
    ]

    assert len(ind_vars) == 1
    ind_var_idx, ind_var = ind_vars[0]
    bound_value = args[1 - ind_var_idx].value

    cmp_op = expr.name.value
    use_if_branch = (cmp_op.startswith("<") and ind_var_idx == 0) or (cmp_op.startswith(">") and ind_var_idx == 1)
    is_strict = not cmp_op.endswith("=")

    return (use_if_branch, is_strict, ind_var, bound_value)


def load_bound_value(bound_value, is_strict, mm: MemoryMap):
    if type(bound_value) is int:
        mm.generate_instruction(Instruction(Opcode.LD, AddressMode.IMM, bound_value))
    elif type(bound_value) is Symbol:
        mm.generate_instruction(Instruction(Opcode.LD, AddressMode.ADDR, bound_value))

    if not is_strict:
        mm.generate_instruction(Instruction(Opcode.ADD, AddressMode.IMM, 1))


def load_vector_loop_bound(is_strict, ind_var, bound_value, mm: MemoryMap, ctx: TranslatorContext):
    load_bound_value(bound_value, is_strict, mm)
    mm.generate_instruction(Instruction(Opcode.SUB, AddressMode.SP, ctx.env.var_sp_offset(ind_var)))
    mm.generate_instruction(Instruction(Opcode.DIV, AddressMode.IMM, VECTOR_SIZE))
    mm.generate_instruction(Instruction(Opcode.MUL, AddressMode.IMM, VECTOR_SIZE))
    mm.generate_instruction(Instruction(Opcode.ADD, AddressMode.SP, ctx.env.var_sp_offset(ind_var)))

    mm.generate_push(ctx)


def jmp_vector_loop_start(vec_start_addr, ind_var, mm: MemoryMap, ctx: TranslatorContext):
    mm.generate_instruction(Instruction(Opcode.LD, AddressMode.SP, ctx.env.var_sp_offset(ind_var)))
    mm.generate_instruction(Instruction(Opcode.ADD, AddressMode.IMM, VECTOR_SIZE))
    mm.generate_instruction(Instruction(Opcode.ST, AddressMode.SP, ctx.env.var_sp_offset(ind_var)))
    mm.generate_instruction(Instruction(Opcode.JMP, AddressMode.IMM, vec_start_addr))


def translate_vector_operand(operand: Expr, ind_var: Symbol, mm: MemoryMap, ctx: TranslatorContext):
    match operand:
        case Value(value=Symbol() as var):
            return AddressMode.SP, ctx.env.var_sp_offset(var), False, False

        case Value(value=int() as num):
            return AddressMode.IMM, num, False, False

        case MemRead(step_size=step_size) if step_size == WORD_SIZE:
            push_mem_access_address(operand, mm, ctx)

            return AddressMode.SP_IND, 0, True, False

        case _:
            translate_vector_expr(operand, ind_var, mm, ctx)
            mm.generate_vpush(ctx)

            return AddressMode.SP, 0, False, True


def translate_vector_func_call(func_call: FuncCall, ind_var: Symbol, mm: MemoryMap, ctx: TranslatorContext):
    op_map = {
        "+": Opcode.VADD,
        "-": Opcode.VSUB,
        "*": Opcode.VMUL,
        "/": Opcode.VDIV,
    }
    name = func_call.name.value

    if name in op_map:
        addr_mode, operand, needs_pop, needs_vpop = translate_vector_operand(func_call.args[1], ind_var, mm, ctx)
        translate_vector_expr(func_call.args[0], ind_var, mm, ctx)

        opcode = op_map[name]
        mm.generate_instruction(Instruction(opcode, addr_mode, operand))

        if needs_pop:
            mm.generate_pop(ctx)
        if needs_vpop:
            mm.generate_vpop(ctx)


VECTOR_BRANCH_PREFIX = "_v_branch"


def translate_vector_cond(cond: Expr, ind_var: Symbol, mm: MemoryMap, ctx: TranslatorContext):
    if type(cond) is not FuncCall or cond.name.value not in CMP_OPERATORS:
        cond = FuncCall(cond.line, Symbol("/="), [cond, Value(cond.line, 0)])

    cmp_map = {
        "=": Opcode.VMEQ,
        "/=": Opcode.VMNE,
        "<": Opcode.VMLT,
        "<=": Opcode.VMLE,
        ">": Opcode.VMGT,
        ">=": Opcode.VMGE,
    }

    if cond.name.value in cmp_map:
        addr_mode, operand, needs_pop, needs_vpop = translate_vector_operand(cond.args[1], ind_var, mm, ctx)
        translate_vector_expr(cond.args[0], ind_var, mm, ctx)

        opcode = cmp_map[cond.name.value]
        mm.generate_instruction(Instruction(opcode, addr_mode, operand))

        if needs_pop:
            mm.generate_pop(ctx)
        if needs_vpop:
            mm.generate_vpop(ctx)


def collect_writes(expr: Expr, root_env: Env, writes: list[Symbol], arr_writes: list[Symbol], ctx: TranslatorContext):
    match expr:
        case LetBlock(_, variables, body):
            for var_init in variables.values():
                collect_writes(var_init, root_env, writes, arr_writes, ctx)

            ctx.push_env(expr)
            for body_expr in body:
                collect_writes(body_expr, root_env, writes, arr_writes, ctx)
            ctx.pop_env()

            return

        case VarSet(name=var):
            var_env = ctx.env.find_env_with(var)
            if not var_env.is_child_of(root_env):
                writes.append(var)

        case MemWrite(ptr=var):
            var_env = ctx.env.find_env_with(var)
            if not var_env.is_child_of(root_env):
                arr_writes.append(var)

    for child in expr.children():
        collect_writes(child, root_env, writes, arr_writes, ctx)


def replace_mem_access(parent: Expr, child: Expr, root_env: Env, arr_writes: list[Symbol], ctx: TranslatorContext):
    new_child: Value | VarSet | None = None
    match child:
        case MemRead(ptr=var) if not ctx.env.find_env_with(var).is_child_of(root_env) and var in arr_writes:
            new_child = Value(child.line, var)

        case MemWrite(ptr=var, value=value) if (
            not ctx.env.find_env_with(var).is_child_of(root_env) and var in arr_writes
        ):
            new_child = VarSet(child.line, var, value)

        case _:
            return child

    parent.replace_child(child, new_child)

    return new_child


def create_fake_expr(new: Expr, root_env: Env, writes: list[Symbol], arr_writes: list[Symbol], ctx: TranslatorContext):
    match new:
        case LetBlock(_, new_vars, new_body):
            for new_var_init in new_vars.values():
                nc = replace_mem_access(new, new_var_init, root_env, arr_writes, ctx)
                create_fake_expr(nc, root_env, writes, arr_writes, ctx)

            ctx.push_env(new)
            for new_body_expr in new_body:
                nc = replace_mem_access(new, new_body_expr, root_env, arr_writes, ctx)
                create_fake_expr(nc, root_env, writes, arr_writes, ctx)
            ctx.pop_env()

            return

        case Value(value=Symbol() as var) if not ctx.env.find_env_with(var).is_child_of(root_env) and (
            var in writes or var in arr_writes
        ):
            new.value = Symbol(VECTOR_BRANCH_PREFIX + var.value)

        case VarSet(name=var) if not ctx.env.find_env_with(var).is_child_of(root_env) and (
            var in writes or var in arr_writes
        ):
            new.name = Symbol(VECTOR_BRANCH_PREFIX + var.value)

    for child in new.children():
        nc = replace_mem_access(new, child, root_env, arr_writes, ctx)
        create_fake_expr(nc, root_env, writes, arr_writes, ctx)


def create_fake_ast(
    expr: Expr, ind_var: Symbol, writes: list[Symbol], arr_writes: list[Symbol], ctx: TranslatorContext
):
    root_let_block = LetBlock(None, {}, [copy.deepcopy(expr)])
    create_fake_expr(root_let_block, ctx.env, writes, arr_writes, ctx)

    tmp_vars: dict[Symbol, Expr] = {Symbol(VECTOR_BRANCH_PREFIX + v.value): Value(None, v) for v in writes}
    tmp_arr_vars: dict[Symbol, Expr] = {
        Symbol(VECTOR_BRANCH_PREFIX + v.value): MemRead(None, v, WORD_SIZE, Value(None, ind_var)) for v in arr_writes
    }
    tmp_var_apply: list[Expr] = [
        VarSet(None, v, Value(None, Symbol(VECTOR_BRANCH_PREFIX + v.value)), True) for v in writes
    ]
    tmp_arr_var_apply: list[Expr] = [
        MemWrite(None, v, WORD_SIZE, Value(None, ind_var), Value(None, Symbol(VECTOR_BRANCH_PREFIX + v.value)), True)
        for v in arr_writes
    ]

    root_let_block.vars = tmp_vars | tmp_arr_vars
    root_let_block.body += tmp_var_apply + tmp_arr_var_apply

    return root_let_block


def translate_vector_if_expr(if_expr: IfExpr, ind_var: Symbol, mm: MemoryMap, ctx: TranslatorContext):
    translate_vector_cond(if_expr.cond, ind_var, mm, ctx)

    if_writes: list[Symbol] = []
    if_arr_writes: list[Symbol] = []
    collect_writes(if_expr.if_branch, ctx.env, if_writes, if_arr_writes, ctx)
    if_branch_ast = create_fake_ast(if_expr.if_branch, ind_var, if_writes, if_arr_writes, ctx)
    translate_vector_expr(if_branch_ast, ind_var, mm, ctx)

    mm.generate_instruction(Instruction(Opcode.VMNOT))

    else_writes: list[Symbol] = []
    else_arr_writes: list[Symbol] = []
    collect_writes(if_expr.else_branch, ctx.env, else_writes, else_arr_writes, ctx)
    else_branch_ast = create_fake_ast(if_expr.else_branch, ind_var, else_writes, else_arr_writes, ctx)
    translate_vector_expr(else_branch_ast, ind_var, mm, ctx)


def translate_vector_let_block(let_block: LetBlock, ind_var: Symbol, mm: MemoryMap, ctx: TranslatorContext):
    ctx.env = Env([], is_vector=True, prev=ctx.env)
    for var_init in reversed(let_block.vars.values()):
        translate_vector_expr(var_init, ind_var, mm, ctx)
        mm.generate_vpush(ctx)
    ctx.pop_env()

    ctx.push_env(let_block)
    ctx.env.is_vector = True

    for expr in let_block.body:
        translate_vector_expr(expr, ind_var, mm, ctx)

    ctx.pop_env()

    for _ in range(len(let_block.vars)):
        mm.generate_instruction(Instruction(Opcode.VPOP))


def translate_vector_expr(expr: Expr, ind_var: Symbol, mm: MemoryMap, ctx: TranslatorContext):
    match expr:
        case Value(value=int() as num):
            mm.generate_instruction(Instruction(Opcode.VLD, AddressMode.IMM, num))

        case Value(value=Symbol() as sym):
            mm.generate_instruction(Instruction(Opcode.VLD, AddressMode.SP, ctx.env.var_sp_offset(sym)))

        case VarSet(name=name, value=value, is_masked=is_masked):
            translate_vector_expr(value, ind_var, mm, ctx)

            opcode = Opcode.VMST if is_masked else Opcode.VST
            mm.generate_instruction(Instruction(opcode, AddressMode.SP, ctx.env.var_sp_offset(name)))

        case MemRead(step_size=step_size) if step_size == WORD_SIZE:
            push_mem_access_address(expr, mm, ctx)
            mm.generate_instruction(Instruction(Opcode.VLD, AddressMode.SP_IND, 0))
            mm.generate_pop(ctx)

        case MemWrite(step_size=step_size, value=value, is_masked=is_masked) if step_size == WORD_SIZE:
            push_mem_access_address(expr, mm, ctx)
            translate_vector_expr(value, ind_var, mm, ctx)

            opcode = Opcode.VMST if is_masked else Opcode.VST
            mm.generate_instruction(Instruction(opcode, AddressMode.SP_IND, 0))

            mm.generate_pop(ctx)

        case FuncCall():
            translate_vector_func_call(expr, ind_var, mm, ctx)

        case IfExpr():
            translate_vector_if_expr(expr, ind_var, mm, ctx)

        case LetBlock():
            translate_vector_let_block(expr, ind_var, mm, ctx)


def translate_vector_code(main_loop, is_strict, ind_var, bound_value, mm: MemoryMap, ctx: TranslatorContext):
    load_vector_loop_bound(is_strict, ind_var, bound_value, mm, ctx)
    vec_start_addr = mm.next_instr_addr()

    mm.generate_instruction(Instruction(Opcode.LD, AddressMode.SP, 0))
    mm.generate_instruction(Instruction(Opcode.CMP, AddressMode.SP, ctx.env.var_sp_offset(ind_var)))
    jmp_vec_end_instr = mm.generate_instruction(Instruction(Opcode.JLE, AddressMode.IMM, None))

    translate_vector_expr(main_loop, ind_var, mm, ctx)
    jmp_vector_loop_start(vec_start_addr, ind_var, mm, ctx)

    mm.instructions[jmp_vec_end_instr].operand = mm.next_instr_addr()
    mm.generate_pop(ctx)


def translate_scalar_code(
    func_name: Symbol, main_loop, is_strict, ind_var, bound_value, mm: MemoryMap, ctx: TranslatorContext
):
    scalar_code_start_addr = mm.next_instr_addr()

    load_bound_value(bound_value, is_strict, mm)
    mm.generate_instruction(Instruction(Opcode.CMP, AddressMode.SP, ctx.env.var_sp_offset(ind_var)))
    jmp_end_instr = mm.generate_instruction(Instruction(Opcode.JLE, AddressMode.IMM, None))

    translate_expr(main_loop, mm, ctx)
    mm.instructions[jmp_end_instr].operand = mm.next_instr_addr()

    for addr, instr in mm.instructions.items():
        if addr >= scalar_code_start_addr and instr.opcode == Opcode.JMP and instr.operand == func_name:
            instr.operand = scalar_code_start_addr


def translate_vector_func_def(func: FuncDef, mm: MemoryMap, ctx: TranslatorContext):
    assert FuncDefFlags.VECTOR in func.flags
    assert len(func.body) == 1

    mm.label_addresses[func.name] = mm.next_instr_addr()

    body_expr = func.body[0]
    assert type(body_expr) is IfExpr

    ctx.push_env(func)

    use_if_branch, is_strict, ind_var, bound_value = extract_vector_info(body_expr.cond, ctx)
    main_loop = body_expr.if_branch if use_if_branch else body_expr.else_branch
    end_code = body_expr.else_branch if use_if_branch else body_expr.if_branch

    translate_vector_code(main_loop, is_strict, ind_var, bound_value, mm, ctx)
    translate_scalar_code(func.name, main_loop, is_strict, ind_var, bound_value, mm, ctx)

    translate_expr(end_code, mm, ctx)

    if FuncDefFlags.INLINE not in func.flags:
        mm.generate_instruction(Instruction(Opcode.RET))

    ctx.pop_env()


def generate_call_start_instr(mm: MemoryMap):
    return Instruction(Opcode.JMP, AddressMode.IMM, mm.program_start_addr)


def generate_call_interrupt_instr(mm: MemoryMap):
    if mm.interrupt_vector is None:
        return Instruction(Opcode.IRET)
    else:
        return Instruction(Opcode.JMP, AddressMode.IMM, mm.interrupt_vector)


def write_binary_header(binary: BinaryIO, mm: MemoryMap):
    write_binary_instruction(binary, generate_call_start_instr(mm), mm)
    write_binary_instruction(binary, generate_call_interrupt_instr(mm), mm)

    if mm.interrupt_vector is None:
        binary.write(bytes(ADDRESS_INSTRUCTION_SIZE - 1))


def write_binary_string_literals(binary: BinaryIO, mm: MemoryMap):
    for str_lit in mm.str_lit_addresses:
        binary.write((str_lit + "\0").encode("ascii"))


def write_binary_allocated_memory(binary: BinaryIO, mm: MemoryMap):
    binary.write(bytes(mm.free_memory_size))


def write_binary_global_vars(binary: BinaryIO, mm: MemoryMap, ctx: TranslatorContext):
    for var in mm.global_vars_addresses:
        value = ctx.global_vars[var].value

        value_int = 0
        match value:
            case Value(value=int() as num):
                value_int = num
            case Value(value=str() as s):
                value_int = mm.str_lit_addresses[s]
            case MemAlloc():
                value_int = mm.global_var_allocs_addresses[var]
            case _:
                raise ParseError(value.line, f"Запрещенный тип значения глобальной переменной: {type(value)}")

        binary.write(to_little_endian(value_int))


def write_binary_instruction(binary: BinaryIO, instr: Instruction, mm: MemoryMap):
    bin_opcode = to_little_endian(instr.opcode.value)[0] << 2

    if instr.opcode in ADDRESSLESS_INSTRUCTIONS:
        binary.write(bytes([bin_opcode]))
    else:
        assert instr.addr_mode is not None
        bin_addr_mode = to_little_endian(instr.addr_mode.value)[0]

        bin_operand = []
        if type(instr.operand) is int:
            bin_operand = to_little_endian(instr.operand)
        elif type(instr.operand) is Symbol:
            if instr.operand in mm.label_addresses and instr.opcode in CONTROL_INSTRUCTIONS:
                addr = mm.label_addresses[instr.operand]
                bin_operand = to_little_endian(addr)
            elif instr.operand in mm.global_vars_addresses:
                addr = mm.global_vars_addresses[instr.operand]
                bin_operand = to_little_endian(addr)
            else:
                raise ParseError(None, f"Неизвестное название: {instr.operand}")

        bin_instr = [bin_opcode + bin_addr_mode] + list(bin_operand)
        binary.write(bytes(bin_instr))


def write_binary(binary: BinaryIO, mm: MemoryMap, ctx: TranslatorContext):
    write_binary_header(binary, mm)
    write_binary_string_literals(binary, mm)
    write_binary_allocated_memory(binary, mm)
    write_binary_global_vars(binary, mm, ctx)

    for instr in mm.instructions.values():
        write_binary_instruction(binary, instr, mm)

    print(f"Binary size: {binary.tell()} B")


def write_debug_header(debug: TextIO, mm: MemoryMap):
    write_debug_instruction(debug, 0, generate_call_start_instr(mm), mm)
    write_debug_instruction(debug, ADDRESS_INSTRUCTION_SIZE, generate_call_interrupt_instr(mm), mm)

    if mm.interrupt_vector is None:
        hex_addr = f"{dec_to_hex(ADDRESS_INSTRUCTION_SIZE + 1):>08}"
        debug.write(f"{hex_addr}:    {'00 00 00 00':<50} \n")


def write_debug_string_literal(debug: TextIO, addr: int, str_lit: str):
    hex_addr = f"{dec_to_hex(addr):>08}"

    str_lit = str_lit + "\\0"
    for sc, escaped in SPECIAL_CHARACTERS.items():
        str_lit = str_lit.replace(sc, escaped)

    debug.write(f"{hex_addr}:    {str_lit:<50} \n")


def write_debug_allocated_memory(debug: TextIO, addr: int, size: int):
    hex_addr = f"{dec_to_hex(addr):>08}"

    if size > 0 and size <= 4:
        debug.write(f"{hex_addr}:    {'00 ' * size:<50} \n")
    elif size > 4:
        debug.write(f"{hex_addr}:    {'00  ...  00':<50} \n")


def write_debug_global_var(debug: TextIO, addr: int, name: str, value: int):
    hex_addr = f"{dec_to_hex(addr):>08}"
    name_str = f"@{name}"

    value_le = hex_little_endian(to_little_endian(value))
    value_str = f"{' '.join(value_le)}"

    debug.write(f"{hex_addr}:    {value_str:<50} {name_str}\n")


def write_debug_instruction(debug: TextIO, addr: int, instr: Instruction, mm: MemoryMap, label: str | None = None):
    hex_addr = f"{dec_to_hex(addr):>08}"
    label_str = "" if label is None else f"@{label}"

    instr_str = ""
    if instr.opcode in ADDRESSLESS_INSTRUCTIONS:
        opcode_hex = dec_to_hex(instr.opcode.value << 2)
        instr_str = f"{opcode_hex:<14}    {instr.opcode.name}"
    else:
        assert instr.addr_mode is not None

        addr_map = {
            AddressMode.IMM: "{}",
            AddressMode.ADDR: "MEM[{}]",
            AddressMode.SP: "MEM[SP + {}]",
            AddressMode.SP_IND: "MEM[MEM[SP + {}]]",
        }

        operand = 0
        operand_hex = []
        if type(instr.operand) is int:
            operand = addr_map[instr.addr_mode].format(f"{instr.operand:X}")
            operand_hex = hex_little_endian(to_little_endian(instr.operand))
        elif type(instr.operand) is Symbol:
            operand = addr_map[instr.addr_mode].format('@' + instr.operand.value)
            if instr.opcode in CONTROL_INSTRUCTIONS:
                operand_hex = hex_little_endian(to_little_endian(mm.label_addresses[instr.operand]))
            else:
                operand_hex = hex_little_endian(to_little_endian(mm.global_vars_addresses[instr.operand]))
        else:
            raise ParseError(None, "Отсутствует операнд инструкции")
        
        opcode_hex = dec_to_hex((instr.opcode.value << 2) + instr.addr_mode.value)
        operand_hex_str = " ".join(operand_hex)
        instr_str = f"{opcode_hex} {operand_hex_str}    {instr.opcode.name} {operand}"

    debug.write(f"{hex_addr}:    {instr_str:<50} {label_str}\n")


def write_debug(debug: TextIO, mm: MemoryMap, ctx: TranslatorContext):
    write_debug_header(debug, mm)

    for str_lit, addr in mm.str_lit_addresses.items():
        write_debug_string_literal(debug, addr, str_lit)

    write_debug_allocated_memory(debug, mm.string_literals_end_addr(), mm.free_memory_size)

    for var, addr in mm.global_vars_addresses.items():
        value = ctx.global_vars[var].value

        value_int = 0
        match value:
            case Value(value=int() as num):
                value_int = num
            case Value(value=str() as s):
                value_int = mm.str_lit_addresses[s]
            case MemAlloc():
                value_int = mm.global_var_allocs_addresses[var]
            case _:
                raise ParseError(value.line, f"Запрещенный тип значения глобальной переменной: {type(value)}")

        write_debug_global_var(debug, addr, var.value, value_int)

    addr_labels = {value: key for key, value in mm.label_addresses.items()}
    for addr, instr in mm.instructions.items():
        if addr in addr_labels:
            write_debug_instruction(debug, addr, instr, mm, addr_labels[addr].value)
        else:
            write_debug_instruction(debug, addr, instr, mm)


STDLIB = "stdlib.lisp"


def translate(src_file: str, bin_file: str, debug_file: str):
    ctx = TranslatorContext()
    mm = MemoryMap()

    stdlib_path = Path(__file__).with_name(STDLIB)
    with stdlib_path.open() as std, open(src_file) as f:
        tokens = tokenize(chain(f, std))
        ast = build_ast(tokens)

        analyze(ast)

        ctx.fill_static_memory(ast)
        ctx.collect_code(ast)
        ctx.create_global_env()

        mm.map_static_memory(ctx)

        for intr in ctx.interrupts.values():
            translate_interrupt(intr, mm, ctx)

        for func in ctx.functions.values():
            if FuncDefFlags.VECTOR in func.flags:
                translate_vector_func_def(func, mm, ctx)
            else:
                translate_func_def(func, mm, ctx)

        mm.program_start_addr = mm.next_instr_addr()
        for expr in ctx.main_program:
            translate_expr(expr, mm, ctx)
        mm.generate_instruction(Instruction(Opcode.HALT))

    print(f"Instructions generated: {len(mm.instructions)}")
    with open(bin_file, "wb") as binary, open(debug_file, "w") as debug:
        write_binary(binary, mm, ctx)
        write_debug(debug, mm, ctx)
