#!/usr/bin/env python3

import re
import argparse
from enum import IntEnum, Flag, auto
from sys import argv, exit

ops = ['=>', 'v', '^', '~'] # order of increasing precedence
atomics = 'abcd'

class Color(IntEnum):
    ''' ANSI terminal colors. '''
    BLACK   = 0
    RED     = 1
    GREEN   = 2
    YELLOW  = 3
    BLUE    = 4
    MAGENTA = 5
    CYAN    = 6
    WHITE   = 7

def ansi_fmt(text, color=None, bold=False, bright=True, underline=False):
    ''' Format given text with ANSI colors, bold, and/or underline. '''
    params = ['1'] * bold + ['4'] * underline
    if color: params.append(f'{9 if bright else 3}{int(color)}')
    return f'\033[{";".join(params)}m{text}\033[m'

def error(pos, message):
    ''' Print syntax error at given position with given message, then exit. '''
    preamble = f'{argv[0]}: ' +\
        ansi_fmt(f'arg={arg+1} pos={pos}', Color.WHITE, True) + ': ' +\
        ansi_fmt(f'Syntax error:', Color.RED, True)
    exit(f'{preamble} {message}')

def missing_operand(pos, token):
    ''' Throws a "missing operand" error for given (operator) token. '''
    error(pos, f"missing operand for '{token}'")

def precedence(op_token):
    ''' Get precedence of given operator token. '''
    if op_token not in ops:
        raise ValueError(f'{op_token} is not an operator')
    return ops.index(op_token)

def scanner(expression):
    ''' Yield tokens from given expression. '''
    if not hasattr(scanner, 'pattern'):
        ops_re = "|".join(map(re.escape, ops))
        scanner.pattern = re.compile(f'{ops_re}|[{atomics}]|\\(|\\)|\s')

    start, length = 0, len(expression)
    if length == 0: error(0, 'empty expression')
    while start < length:
        match = scanner.pattern.match(expression, start)
        if not match:
            nxt = scanner.pattern.search(expression, start)
            if nxt: expression = expression[:nxt.start(0)]
            error(start, f"invalid token '{expression[start:]}'")

        token = match.group(0)
        if not token.isspace(): yield start, token
        start = match.end(0)

def not_after_exp(pos, token, last_token):
    ''' Asserts that current token does not follow an expression (without
        interposing operator). '''
    if last_token in atomics + ')':
        error(pos, f"missing operator between '{last_token}' and '{token}'")

def not_after_op(last_pos, last_token):
    ''' Asserts that current token does not follow an operator. '''
    if last_token in ops: missing_operand(last_pos, last_token)

def parse_rpn(expression):
    ''' Parse expression to RPN using shunting yard algorithm, yielding output. '''
    expression = expression.lower()
    stack = []
    last_pos, last_tok = -1, chr(0)

    for pos, token in scanner(expression):
        if token in atomics:
            not_after_exp(pos, token, last_tok)
            yield pos, token
        elif token == '(':
            not_after_exp(pos, token, last_tok)
            stack.append((pos, token))
        elif token == ')':
            if last_tok == '(': error(pos, f'empty parentheses')
            not_after_op(last_pos, last_tok)

            try:
                top_pos, top = stack.pop()
                while top != '(':
                    yield top_pos, top
                    top_pos, top = stack.pop()
            except IndexError: error(pos, 'unpaired closing parenthesis')
        else: # token is operator
            if token == '~': not_after_exp(pos, token, last_tok)
            else:
                if last_tok == '(': missing_operand(pos, token)
                not_after_op(last_pos, last_tok)

            tok_prec = precedence(token)
            try:
                while precedence(stack[-1][1]) >= tok_prec: yield stack.pop()
            except (IndexError, ValueError): pass # no operator on top of stack
            stack.append((pos, token))

        last_pos, last_tok = pos, token

    not_after_op(last_pos, last_tok) # cannot end in operator
    while stack:
        if stack[-1][1] == '(': error(stack[-1][0], 'unpaired opening parenthesis')
        yield stack.pop()

def compile_to_python(expression):
    ''' Convert given propositional logic expression to fully-parenthesized valid
        Python logic expression for use in eval(). '''
    result = []
    for pos, token in parse_rpn(expression):
        if token not in ops: result.append(token)
        else:
            try:
                right = result.pop()
                if token == '~': result.append(f'(not {right})')
                else:
                    prefix = 'not ' * (token == '=>')
                    infix = 'and' if token == '^' else 'or'
                    result.append(f'({prefix}{result.pop()} {infix} {right})')
            except IndexError: missing_operand(pos, token)

    compiled = result.pop()
    return compiled[1:-1] if compiled[0] == '(' else compiled

def valuate(proposition, atomic_vals):
    ''' Given truth of each atomic, valuate truth of given proposition. '''
    binary_vals = f'{atomic_vals:0{len(atomics)}b}'
    for atomic, val in zip(atomics, binary_vals):
        exec(f'{atomic}=bool({val})')
    return eval(proposition)

class Implied(Flag):
    ''' Possible relationships between premises & conclusion. '''
    VACUOUS = 0
    PROVEN = auto()
    DISPROVEN = auto()
    UNPROVEN = PROVEN | DISPROVEN

    def __str__(self):
        ''' Get colored symbolic representation of relationship. '''
        return [
            ansi_fmt('~', Color.MAGENTA),
            ansi_fmt('\u2713', Color.GREEN),
            ansi_fmt('\u2717', Color.RED),
            ansi_fmt('?', Color.YELLOW)][self.value]

def implied_atomics(premises):
    ''' Returns whether each atomic proposition is (dis)proven by, or independent of
        given premises. We use semantic implication rather than deduction. '''
    implied = {atomic: Implied.VACUOUS for atomic in atomics}
    for atomic_vals in range(2**len(atomics)):
        if all(map(lambda p: valuate(p, atomic_vals), premises)):
            for atomic in atomics:
                atomic_val = valuate(atomic, atomic_vals)
                implied[atomic] |= Implied[('DISPROVEN', 'PROVEN')[atomic_val]]
    return implied

def print_summary(premises, print_compiled=False, plain_text=False):
    ''' Print summary of atomic propositions (dis)proven by given premises. '''
    implied = implied_atomics(premises)
    if print_compiled:
        print('Compiled premises:')
        for i, premise in enumerate(premises): print(f' {i+1}. {premise}')
    if plain_text:
        fltr = lambda p: lambda a: implied[a] in ([Implied.PROVEN,
            Implied.VACUOUS] if p else [Implied.DISPROVEN])
        proven = ', '.join(filter(fltr(True), atomics)) or '(none)'
        if implied[atomics[0]] == Implied.VACUOUS: proven += ', vacuously'
        disproven = ', '.join(filter(fltr(False), atomics)) or '(none)'
        print(f'Proven: {proven}\nDisproven: {disproven}')
    else:
        if print_compiled: print('Implications:')
        print(' ' + '\t'.join(map(lambda i: f'{i[0]} [{i[1]}]', implied.items())))

if __name__ == '__main__':
    # Create argument parser and parse args
    parser = argparse.ArgumentParser(
        description="Lists atomic propositions which are (dis)proven by given list\
            of premises. Premises must be valid sentences built from parentheses;\
            atomics, A, B, C, and D; and operators, '~', '^', 'v', and '=>'\
            (standard interpretations and precedence).",
        epilog='Designed as an aid for the card game Ergo.')
    parser.add_argument('premise', nargs='+', help='one of the logical premises')
    parser.add_argument('-c', '--print-compiled', action='store_true',
        help='print the compiled premises')
    parser.add_argument('-t', '--plain-text', action='store_true',
        help='print implications in plain text (vs. symbolically)')
    args = parser.parse_args()

    # Execute relevant functions
    premises = []
    for arg, premise in enumerate(args.premise):
        premises.append(compile_to_python(premise))
    print_summary(premises, args.print_compiled, args.plain_text)
