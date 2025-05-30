import sys
import os
import subprocess
import argparse
from typing import Any, Dict, List, Tuple

# ===== Types =====
class Type:
    INT = 'int'
    FLOAT = 'float'
    BOOL = 'bool'
    STR = 'str'
    LIST = 'list'
    VOID = 'void'

# ===== Value =====
class Value:
    def __init__(self, value: Any):
        self.value = value

    def get_type(self) -> str:
        if isinstance(self.value, bool): return Type.BOOL
        if isinstance(self.value, int): return Type.INT
        if isinstance(self.value, float): return Type.FLOAT
        if isinstance(self.value, str): return Type.STR
        if isinstance(self.value, list): return Type.LIST
        return Type.VOID

# ===== AST Nodes =====
class Expr: pass
class Stmt: pass

class Literal(Expr):
    def __init__(self, value: Value): self.value = value

class VarRef(Expr):
    def __init__(self, name: str): self.name = name

class TypedExpr(Expr):
    def __init__(self, typ: str, expr: Expr):
        self.typ = typ
        self.expr = expr

class BinOp(Expr):
    def __init__(self, left: Expr, op: str, right: Expr):
        self.left, self.op, self.right = left, op, right

class Call(Expr):
    def __init__(self, name: str, args: List[Expr]): self.name, self.args = name, args

class VarDef(Stmt):
    def __init__(self, name: str, typ: str, expr: Expr):
        self.name, self.typ, self.expr = name, typ, expr

class Print(Stmt):
    def __init__(self, exprs: List[Expr]): self.exprs = exprs

class Shell(Stmt):
    def __init__(self, command: str): self.command = command

class SetShell(Stmt):
    def __init__(self, shell: str): self.shell = shell

class PythonExec(Stmt):
    def __init__(self, code: str): self.code = code

class Exit(Stmt): pass
class Clear(Stmt): pass
class Help(Stmt): pass
class ExprStmt(Stmt):
    def __init__(self, expr: Expr): self.expr = expr

# ===== Context =====
class Context:
    def __init__(self):
        self.vars: Dict[str, Any] = {}
        self.types: Dict[str, str] = {}
        self.shell: str = '/bin/sh'

        # Register built-in 'print' function so calls like print(str:"Hello World") work
        self.vars['print'] = self.builtin_print

    def builtin_print(self, *args):
        # Convert all args to string and print separated by spaces
        print(*args)
        return None

# ===== Tokenizer =====
def tokenize(line: str) -> List[str]:
    toks, cur, in_str = [], '', False
    for c in line:
        if c == '"':
            in_str = not in_str
            cur += c
        elif c.isspace() and not in_str:
            if cur:
                toks.append(cur)
                cur = ''
        else:
            cur += c
    if cur:
        toks.append(cur)
    return toks

def parse_expr(s: str) -> Expr:
    s = s.strip()

    # Handle binary operations FIRST
    # Don't break on colon yet
    for op in ['+', '-', '*', '/', '==', '!=', '<', '>']:
        parts = s.split(op)
        if len(parts) == 2:
            left, right = parts
            return BinOp(parse_expr(left), op, parse_expr(right))

    # Now handle type annotation
    if ':' in s and not s.startswith('"') and not s[0].isdigit():
        typ, rest = s.split(':', 1)
        if typ in (Type.INT, Type.FLOAT, Type.BOOL, Type.STR, Type.LIST):
            return TypedExpr(typ, parse_expr(rest.strip()))

    # Now handle literals and calls
    if s.startswith('"') and s.endswith('"'):
        return Literal(Value(s[1:-1]))
    if s.replace('.', '', 1).isdigit() and s.count('.') == 1:
        return Literal(Value(float(s)))
    if s.isdigit():
        return Literal(Value(int(s)))
    if s.endswith(')') and '(' in s:
        idx = s.find('(')
        name = s[:idx].strip()
        args_str = s[idx+1:-1].strip()
        args = []
        if args_str:
            args = [parse_expr(arg.strip()) for arg in args_str.split(',')]
        return Call(name, args)
    return VarRef(s)

def parse_stmt(line: str) -> Stmt:
    if line in ('exit', 'quit'):
        return Exit()
    if line == 'clear':
        return Clear()
    if line == 'help':
        return Help()
    if line.startswith('chsh '):
        return SetShell(line[5:].strip())
    if line.startswith('sh '):
        return Shell(line[3:].strip())
    if line.startswith('py '):
        return PythonExec(line[3:].strip())
    if line.startswith('var '):
        _, rest = line.split('var ', 1)
        if '=' not in rest:
            raise SyntaxError("Variable declaration must have '='")
        name_type, expr = rest.split('=', 1)
        name_type = name_type.strip()
        expr = expr.strip()
        if ':' in name_type:
            name, typ = name_type.split(':', 1)
            return VarDef(name.strip(), typ.strip(), parse_expr(expr))
        else:
            name = name_type
            return VarDef(name.strip(), None, parse_expr(expr))
    if line.startswith('print(') and (line.endswith(')py') or line.endswith(')sh')):
        args = line[6:-3]
        return Print([parse_expr(a.strip()) for a in args.split(',') if a.strip()])
    return ExprStmt(parse_expr(line))

def parse_program(src: str) -> List[Stmt]:
    lines = src.splitlines()
    stmts = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # === Skip dsd(...)dsd blocks as comments ===
        # ===== Handle dsd-style comments =====
        if line.startswith('dsd('):
            # Multiline dsd(...)dsd block
            while i < len(lines) and not lines[i].strip().endswith(')dsd'):
                i += 1
            i += 1  # Skip the line with ')dsd'
            continue

        elif line.startswith('dsd '):
            # Single-line dsd comment: dsd yes "No"
            i += 1
            continue

        # === Skip regular comments ===
        if not line or line.startswith('#'):
            i += 1
            continue

        # Handle multiline Python block
        if line == 'py(':
            i += 1
            code_lines = []
            while i < len(lines) and lines[i].strip() != ')py':
                code_lines.append(lines[i])
                i += 1
            stmts.append(PythonExec('\n'.join(code_lines)))
            i += 1  # Skip the closing )py
            continue

        # Handle multiline Shell block
        if line == 'sh(':
            i += 1
            code_lines = []
            while i < len(lines) and lines[i].strip() != ')sh':
                code_lines.append(lines[i])
                i += 1
            stmts.append(Shell('\n'.join(code_lines)))
            i += 1  # Skip the closing )sh
            continue

        # Handle regular statements (including inline py/sh/var/etc.)
        parts = [part.strip() for part in line.split(';') if part.strip()]
        for part in parts:
            stmts.append(parse_stmt(part))
        i += 1

    return stmts

# ===== Type Checker =====
def type_check_expr(e: Expr, ctx: Context) -> str:
    if isinstance(e, TypedExpr):
        t = e.typ
        expr_t = type_check_expr(e.expr, ctx)
        if t != expr_t:
            raise TypeError(f"Type annotation {t} does not match expression type {expr_t}")
        return t
    if isinstance(e, Literal):
        return e.value.get_type()
    if isinstance(e, VarRef):
        return ctx.types.get(e.name, Type.VOID)
    if isinstance(e, BinOp):
        lt = type_check_expr(e.left, ctx)
        rt = type_check_expr(e.right, ctx)
        if lt != rt:
            raise TypeError(f"Type mismatch {lt} {e.op} {rt}")
        return lt
    if isinstance(e, Call):
        # We don't do strict return type check here for simplicity
        return Type.VOID
    return Type.VOID

def type_check_stmt(s: Stmt, ctx: Context):
    if isinstance(s, VarDef):
        t = type_check_expr(s.expr, ctx)
        if t != s.typ:
            raise TypeError(f"Expected {s.typ}, got {t}")
        ctx.types[s.name] = s.typ

# ===== Executor =====
def execute_stmt(s: Stmt, ctx: Context):
    if isinstance(s, Exit):
        sys.exit(0)
    if isinstance(s, Clear):
        os.system('cls' if os.name == 'nt' else 'clear')
    if isinstance(s, Help):
        print("DolphinScript Help:\n  var x:type = expr\n  print(expr)\n  sh command\n  py code\n  exit\n  help\n  clear")
    elif isinstance(s, SetShell):
        ctx.shell = s.shell
    elif isinstance(s, Shell):
        subprocess.call(s.command, shell=True, executable=ctx.shell)
    elif isinstance(s, PythonExec):
        exec(s.code, {}, ctx.vars)
    elif isinstance(s, VarDef):
        val = eval_expr(s.expr, ctx)
        ctx.vars[s.name] = val.value
        ctx.types[s.name] = s.typ
    elif isinstance(s, Print):
        # First, ensure all expressions are typed
        for e in s.exprs:
            if not isinstance(e, TypedExpr):
                print("Error: Please use types in print statements")
                sys.exit(1)  # Or raise TypeError(...)
    # Now evaluate and print since all passed
        out_vals = []
        for e in s.exprs:
            val = eval_expr(e.expr, ctx)
            out_vals.append(str(val.value))
        print(' '.join(out_vals))
    elif isinstance(s, ExprStmt):
        eval_expr(s.expr, ctx)

def eval_expr(e: Expr, ctx: Context) -> Value:
    # Literal: directly return its Value
    if isinstance(e, Literal):
        return e.value

    # Variable reference: look up in context
    if isinstance(e, VarRef):
        if e.name in ctx.vars:
            val = ctx.vars[e.name]
            return val if isinstance(val, Value) else Value(val)
        else:
            raise RuntimeError(f"Undefined variable: {e.name}")

    # Function call: fetch from context and invoke
    if isinstance(e, Call):
        if e.name not in ctx.vars:
            raise RuntimeError(f"Unknown function: {e.name}")
        func = ctx.vars[e.name]
        if not callable(func):
            raise RuntimeError(f"{e.name} is not callable")
        args = [eval_expr(arg, ctx).value for arg in e.args]
        result = func(*args)
        return result if isinstance(result, Value) else Value(result)

    # Typed expression: evaluate inner expr and cast
    if isinstance(e, TypedExpr):
        inner = eval_expr(e.expr, ctx).value
        try:
            if e.typ == Type.INT:
                return Value(int(inner))
            if e.typ == Type.FLOAT:
                return Value(float(inner))
            if e.typ == Type.BOOL:
                return Value(bool(inner))
            if e.typ == Type.STR:
                return Value(str(inner))
            if e.typ == Type.LIST:
                return Value(inner if isinstance(inner, list) else [inner])
            return Value(inner)
        except Exception as ex:
            raise TypeError(f"Cannot convert {inner} to {e.typ}: {ex}")

    # Binary operation: evaluate operands and apply operator
    if isinstance(e, BinOp):
        left_val = eval_expr(e.left, ctx).value
        right_val = eval_expr(e.right, ctx).value
        op = e.op
        try:
            if op == '+':    return Value(left_val + right_val)
            if op == '-':    return Value(left_val - right_val)
            if op == '*':    return Value(left_val * right_val)
            if op == '/':    return Value(left_val / right_val)
            if op == '==':   return Value(left_val == right_val)
            if op == '!=':   return Value(left_val != right_val)
            if op == '<':    return Value(left_val < right_val)
            if op == '<=':   return Value(left_val <= right_val)
            if op == '>':    return Value(left_val > right_val)
            if op == '>=':   return Value(left_val >= right_val)
            raise RuntimeError(f"Unsupported operator: {op}")
        except Exception as ex:
            raise RuntimeError(f"Error in binary operation {left_val} {op} {right_val}: {ex}")

    # Fallback for unknown expressions
    raise RuntimeError(f"Unknown expression type: {type(e)}")

# ===== Main REPL =====
def repl():
    ctx = Context()
    print("Welcome to DolphinScript REPL. Type 'help' for commands.")
    while True:
        try:
            line = input('>>> ').strip()
            if not line:
                continue
            stmts = parse_program(line)
            for stmt in stmts:
                type_check_stmt(stmt, ctx)
                execute_stmt(stmt, ctx)
        except Exception as e:
            print(f"Error: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsa', action='store_true', help='Package this script’s folder using the `dsa` command')
    args, unknown = parser.parse_known_args()

    if args.dsa:
        # Get directory of this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        folder_name = os.path.basename(script_dir)
        output_file = os.path.join(script_dir, f"{folder_name}.dsa")

        # Run system `dsa` command to package this directory
        result = subprocess.run(["./dsa", "a", script_dir, output_file])

        if result.returncode == 0:
            print(f"Package created: {output_file}")
        else:
            print("Error: Failed to create DSA package.")
        sys.exit(result.returncode)

    # Normal execution (REPL or file)
    ctx = Context()

    if len(sys.argv) > 1:
        filename = sys.argv[1]
        with open(filename, 'r') as f:
            src = f.read()
        stmts = parse_program(src)
        for stmt in stmts:
            type_check_stmt(stmt, ctx)
            execute_stmt(stmt, ctx)
    else:
        print("DolphinScript REPL (type 'exit' or 'quit' to exit)")
        while True:
            try:
                line = input('>>> ').strip()
                if not line:
                    continue
                stmts = parse_program(line)
                for stmt in stmts:
                    type_check_stmt(stmt, ctx)
                    execute_stmt(stmt, ctx)
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break
            except Exception as e:
                print(f"Error: {e}")

if __name__ == '__main__':
    main()