# PipesHub Code Review Style Guide

## Review Instructions for Gemini

This project enforces compiled-language-level strictness for Python and Node.js/TypeScript. When reviewing a PR:

1. **Only review changed and new code in the PR diff.** Do NOT flag violations in unchanged lines or existing code that was not touched in the PR.
2. **Flag every violation** in changed/new lines as a review comment, even if there are many. Use `comment_severity_threshold: LOW`.
3. **Mark severity** on each comment: `CRITICAL` (type safety, null safety, async safety), `HIGH` (bug patterns, return consistency), `MEDIUM` (style, performance, simplification), `LOW` (import ordering, naming).
4. **Include the rule name** in your comment (e.g., "Violation: no-floating-promises" or "Violation: ANN401 — Any banned").
5. **Suggest the fix** with a code snippet showing the correct pattern.
6. **Do NOT comment on pre-existing code** that was not modified in this PR — even if it has violations. Only new and changed lines are in scope.

---

## General Principles

- Keep functions focused and small. Each function should do one thing well.
- Prefer clarity over cleverness. Code is read far more often than it is written.
- Avoid dead code, unused imports, and commented-out blocks in production code.
- Handle errors explicitly. Do not silently swallow exceptions.
- Use meaningful variable and function names that convey intent.

---

# Python Rules

## 1. Type Annotations (CRITICAL)

### 1.1 All function parameters must have type annotations [ANN001, ANN002, ANN003, reportMissingParameterType]
```python
# BAD
def process(data, config):
def fetch(*args, **kwargs):

# GOOD
def process(data: Document, config: IndexConfig) -> ProcessResult:
def fetch(*args: str, **kwargs: int) -> Response:
```

### 1.2 All functions must have return type annotations [ANN201, ANN202, ANN204, ANN205, ANN206]
```python
# BAD
def get_name(user):
    return user.name
class Foo:
    def __init__(self):

# GOOD
def get_name(user: User) -> str:
    return user.name
class Foo:
    def __init__(self) -> None:
```

### 1.3 Do NOT use `typing.Any` in annotations [ANN401]
```python
# BAD
def process(data: Any) -> Any:
self.filters: Any = None

# GOOD
def process(data: Document) -> ProcessResult:
self.filters: ZammadFilters | None = None
```

### 1.4 Use modern type syntax (Python 3.10+) [UP006, UP007, UP035]
```python
# BAD — deprecated typing imports
from typing import List, Dict, Optional, Union, Tuple, Set
x: List[int]
y: Optional[str]
z: Union[str, int]

# GOOD — builtin types + union operator
x: list[int]
y: str | None
z: str | int
```

### 1.5 Generic types must include type arguments [reportMissingTypeArgument]
```python
# BAD
items: list = []
data: dict = {}
cache: Dict = {}

# GOOD
items: list[str] = []
data: dict[str, int] = {}
cache: dict[str, CacheEntry] = {}
```

## 2. Null Safety (CRITICAL) [reportOptionalMemberAccess, reportOptionalCall, reportOptionalIterable, reportOptionalSubscript, reportOptionalContextManager, reportOptionalOperand]

Never call methods, access attributes, iterate, subscript, or use operators on values that could be `None` without a guard.
```python
# BAD
user = get_user()  # returns User | None
print(user.name)   # crash if None
for item in maybe_list:  # crash if None
result = maybe_dict["key"]  # crash if None

# GOOD
user = get_user()
if user is not None:
    print(user.name)
for item in (maybe_list or []):
    ...
result = maybe_dict["key"] if maybe_dict else default
```

## 3. Variable & Assignment Type Safety (CRITICAL) [reportAssignmentType, reportReturnType, reportUnknownVariableType, reportUnknownMemberType]

### 3.1 Assignments must match declared type
```python
# BAD
x: int = "hello"
name: str = 42

# GOOD
x: int = 5
name: str = "hello"
```

### 3.2 Return values must match annotation
```python
# BAD
def get_count() -> int:
    return "five"

# GOOD
def get_count() -> int:
    return 5
```

### 3.3 Variables holding custom objects must be typed — never use `object` or `Any`
```python
# BAD
config = await self.get_auth_details()  # Unknown type
factory_result: object = factory.create()

# GOOD
config: ZammadAuthConfig = await self.get_auth_details()
factory_result: SlackClient = factory.create()
```

### 3.4 Factory methods must return specific types, not `object` [reportAttributeAccessIssue]
```python
# BAD
def create_client(self) -> object:

# GOOD
def create_client(self) -> SlackClient:
# OR with generics
T = TypeVar("T")
def create_client(self) -> T:
```

## 4. Call-Site Validation (CRITICAL) [reportCallIssue, reportArgumentType, reportIndexIssue, reportOperatorIssue, reportAttributeAccessIssue, reportUnknownArgumentType]

```python
# BAD — wrong number of args
def greet(name: str) -> str: ...
greet("Alice", "Bob")  # too many args

# BAD — wrong arg type
def add(a: int, b: int) -> int: ...
add("1", "2")  # str not int

# BAD — bad attribute access (typo)
user.nmae  # should be user.name

# BAD — bad operator
"hello" - 5  # str doesn't support subtraction

# BAD — bad index
my_dict: dict[str, int] = {}
my_dict[123]  # key should be str
```

## 5. Return Statement Consistency (HIGH) [RET501, RET502, RET503, RET504]

### 5.1 All code paths must return explicitly when function has return value
```python
# BAD — implicit None return when found is False [RET502, RET503]
def get_name(found: bool) -> str:
    if found:
        return "alice"
    # falls off → returns None

# GOOD
def get_name(found: bool) -> str:
    if found:
        return "alice"
    return ""
```

### 5.2 Do not explicitly return None if it's the only return [RET501]
```python
# BAD
def setup() -> None:
    do_things()
    return None

# GOOD
def setup() -> None:
    do_things()
```

### 5.3 No unnecessary variable before return [RET504]
```python
# BAD
def get_value() -> int:
    result = compute()
    return result

# GOOD
def get_value() -> int:
    return compute()
```

## 6. Async Safety (CRITICAL) [reportUnusedCoroutine, ASYNC100, ASYNC110]

### 6.1 Every async call must be awaited
```python
# BAD — coroutine created but never executed
async_fetch(url)

# GOOD
await async_fetch(url)
```

### 6.2 Async blocking calls must have timeouts [ASYNC100]
```python
# BAD — can hang forever
await asyncio.sleep(delay)
await event.wait()

# GOOD
await asyncio.wait_for(event.wait(), timeout=30)
```

## 7. Override & Inheritance Safety (CRITICAL) [reportImplicitOverride, reportIncompatibleMethodOverride, reportIncompatibleVariableOverride, reportMissingSuperCall, reportUnsafeMultipleInheritance]

### 7.1 Methods overriding parent MUST use @override decorator
```python
# BAD
class ZammadConnector(BaseConnector):
    async def init(self):  # silently overrides parent
        ...

# GOOD
from typing import override

class ZammadConnector(BaseConnector):
    @override
    async def init(self) -> None:
        ...
```

### 7.2 Overrides must preserve parent method signature
```python
# BAD — parent takes str, child takes int
class Parent:
    def process(self, data: str) -> None: ...
class Child(Parent):
    def process(self, data: int) -> None: ...  # breaks parent contract

# GOOD
class Child(Parent):
    @override
    def process(self, data: str) -> None: ...
```

### 7.3 Subclass __init__ must call super().__init__()
```python
# BAD
class MyConnector(BaseConnector):
    def __init__(self) -> None:
        self.custom = "value"  # forgot super → logger uninitialized

# GOOD
class MyConnector(BaseConnector):
    def __init__(self) -> None:
        super().__init__()
        self.custom = "value"
```

## 8. Bug Detection Patterns (HIGH) [B006, B007, B011, B012, B015, B016, B017, B018, B020, B023, B025, B029, B032, B904]

### 8.1 No mutable default arguments [B006]
```python
# BAD — all callers share the same list
def process(items=[]):
def process(config={}):

# GOOD
def process(items: list[str] | None = None):
    items = items or []
```

### 8.2 Use _ prefix for unused loop variables [B007]
```python
# BAD
for item in range(10):
    do_something()

# GOOD
for _item in range(10):
    do_something()
```

### 8.3 No return/break/continue in finally block [B012]
```python
# BAD — swallows the exception
try:
    risky()
finally:
    return default  # exception silently lost
```

### 8.4 Closure variables must be bound in loop body [B023]
```python
# BAD — all lambdas capture same i, all return 4
for i in range(5):
    fns.append(lambda: i)

# GOOD — bind i as default arg
for i in range(5):
    fns.append(lambda i=i: i)
```

### 8.5 Use raise ... from inside except blocks [B904]
```python
# BAD — original traceback lost
try:
    data[key]
except KeyError:
    raise ValueError("bad key")

# GOOD
except KeyError as e:
    raise ValueError("bad key") from e
```

### 8.6 No bare except or try-except-pass [B025]
```python
# BAD
try:
    risky()
except:
    pass

# GOOD
try:
    risky()
except SpecificError as e:
    logger.warning("Failed: %s", e)
```

### 8.7 No assert False — use raise AssertionError [B011]
### 8.8 No raise literal — use raise Exception(...) [B016]
### 8.9 No pointless comparison without using result [B015]
### 8.10 No useless expressions (statements with no effect) [B018]
### 8.11 No loop variable overwritten inside loop body [B020]
### 8.12 No walrus operator typos (x: int vs x := int) [B032]

## 9. Pylint Error & Warning Patterns (HIGH) [PLE, PLW, PLR]

### 9.1 __init__ must not return a value [PLE0100, PLE0101]
### 9.2 No continue in finally block [PLE0116]
### 9.3 No nonlocal at module level [PLE0117]
### 9.4 No duplicate base classes [PLE0241]
### 9.5 Special methods must have correct signatures [PLE0302]
### 9.6 __all__ must contain only valid string entries [PLE0604, PLE0605]
### 9.7 No await outside async function [PLE1142]
### 9.8 No useless else on loop (for/else, while/else) [PLW0120]
### 9.9 No self-assigning variables (x = x) [PLW0127]
### 9.10 No assert on string literal (always truthy) [PLW0129]
### 9.11 No global statements — pass values as parameters [PLW0602, PLW0603]
### 9.12 No binary operations in exception clause (except A or B → except (A, B)) [PLW0711]
### 9.13 os.environ.get default must be str, not int/bool [PLW1508]
### 9.14 Do not redefine loop variable by assignment inside loop [PLW2901]
### 9.15 Property decorators must not take positional args [PLR0206]
### 9.16 Do not redefine function argument with local variable of same name [PLR1704]
### 9.17 No useless return at end of function (return None at end) [PLR1711]

## 10. Duplicate & Enum Safety (HIGH) [PIE794, PIE796, PIE810]

```python
# BAD — duplicate field
class Config:
    timeout = 30
    timeout = 60  # silently overwrites

# BAD — non-unique enum values
class Status(Enum):
    ACTIVE = 1
    ENABLED = 1  # same value, different name

# BAD — multiple startswith
if s.startswith("http") or s.startswith("https"):

# GOOD
if s.startswith(("http", "https")):
```

## 11. Performance Patterns (MEDIUM) [PERF101, PERF102, PERF401, PERF403, C4]

```python
# BAD [PERF101] — unnecessary list() in for loop
for item in list(generator):

# GOOD
for item in generator:

# BAD [PERF401] — use list comprehension
items = []
for x in data:
    items.append(transform(x))

# GOOD
items = [transform(x) for x in data]

# BAD [C4] — unnecessary wrapper
list([1, 2, 3])
dict({"a": 1})

# GOOD
[1, 2, 3]
{"a": 1}
```

## 12. Simplification Patterns (MEDIUM) [SIM102, SIM105, SIM108, SIM110, SIM112, SIM118, SIM201, SIM910]

```python
# BAD [SIM102] — collapsible if
if a:
    if b:
        do()
# GOOD
if a and b:
    do()

# BAD [SIM105] — use contextlib.suppress
try:
    do()
except FileNotFoundError:
    pass
# GOOD
with contextlib.suppress(FileNotFoundError):
    do()

# BAD [SIM118] — key in dict.keys()
if key in my_dict.keys():
# GOOD
if key in my_dict:

# BAD [SIM910] — redundant None default
d.get(key, None)
# GOOD
d.get(key)
```

## 13. Boolean Arguments (HIGH) [FBT001, FBT002]

```python
# BAD — caller writes fetch("url", True, False) — unreadable
def fetch(url: str, verify: bool, follow: bool):

# GOOD — keyword-only booleans
def fetch(url: str, *, verify: bool = True, follow: bool = True):
```

## 14. Builtin Shadowing (HIGH) [A001, A002]

Never use these as variable or argument names: `id`, `type`, `list`, `dict`, `input`, `format`, `hash`, `map`, `filter`, `set`, `str`, `int`, `bytes`, `object`, `range`, `next`, `iter`, `zip`, `any`, `all`, `sum`, `min`, `max`, `open`, `print`, `len`, `tuple`, `bool`, `float`, `complex`, `property`, `super`, `staticmethod`, `classmethod`.

## 15. No Print in Production Code (HIGH) [T201, T203]

```python
# BAD
print("debug:", value)
pprint(data)

# GOOD
logger.info("Processing: %s", value)
logger.debug("Data: %s", data)
```

## 16. Timezone-Aware Datetimes (HIGH) [DTZ001-DTZ012]

```python
# BAD — all of these create naive datetimes
datetime.now()
datetime.utcnow()
datetime.today()
datetime.fromtimestamp(ts)
datetime.utcfromtimestamp(ts)
datetime.strptime(s, fmt)  # without %z in fmt
date.today()
datetime.fromisoformat(s)  # without timezone

# GOOD
from datetime import datetime, timezone, date
datetime.now(tz=timezone.utc)
datetime.fromtimestamp(ts, tz=timezone.utc)
datetime.strptime(s, "%Y-%m-%d %H:%M:%S%z")
```

## 17. Type-Checking Imports (MEDIUM) [TC001, TC002, TC003]

```python
# BAD — HeavyModel imported at runtime but only used in type hints
from app.models import HeavyModel
def process(data: HeavyModel) -> None: ...

# GOOD — deferred import
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.models import HeavyModel
def process(data: HeavyModel) -> None: ...
```

## 18. Import Ordering (LOW) [I]

Imports must be sorted: stdlib → third-party → first-party, with blank lines between groups. Ruff auto-fixes this with isort-compatible ordering.

## 19. Dead Code & Unreachable Code (HIGH) [reportUnreachable, reportUnnecessaryIsInstance, reportUnnecessaryComparison, reportUnnecessaryContains, reportUnnecessaryCast, reportUnnecessaryTypeIgnoreComment]

```python
# BAD — code after return/raise never runs [reportUnreachable]
def process():
    return result
    cleanup()  # dead code

# BAD — isinstance check always true [reportUnnecessaryIsInstance]
x: str = "hello"
if isinstance(x, str):  # always true

# BAD — stale type: ignore comment [reportUnnecessaryTypeIgnoreComment]
x: int = 5  # type: ignore  # no error to ignore
```

## 20. Ruff-Specific Patterns (MEDIUM) [RUF005, RUF015, RUF019, RUF100]

```python
# BAD [RUF005] — concatenation
combined = list_a + list_b + [extra]
# GOOD
combined = [*list_a, *list_b, extra]

# BAD [RUF015] — allocating iterable for first element
first = list(generator)[0]
# GOOD
first = next(iter(generator))

# BAD [RUF019] — unnecessary key check before access
if key in d:
    value = d[key]
# GOOD
value = d.get(key)

# BAD [RUF100] — unused noqa directive
x = 5  # noqa: F841  ← but x IS used below
```

## 21. Miscellaneous Pyright Rules (MEDIUM-HIGH)

- **No implicit string concatenation** [reportImplicitStringConcatenation]: `items = ["a" "b", "c"]` — missing comma, list has 2 items not 3.
- **No constant redefinition** [reportConstantRedefinition]: `MAX_RETRIES = 3` then later `MAX_RETRIES = 10` — silent override.
- **Use typing.NamedTuple not collections.namedtuple** [reportUntypedNamedTuple]: Fields get proper types.
- **No accessing _private members from outside** [reportPrivateUsage]: Coupling to implementation details.
- **No deprecated API usage** [reportDeprecated]: Flag calls to deprecated functions.
- **No circular imports** [reportImportCycles]: Move type-only imports behind TYPE_CHECKING.
- **Match/case must handle all cases** [reportMatchNotExhaustive]: Like switch exhaustiveness.
- **No unused imports** [reportUnusedImport]: Remove imports that are not used.
- **No unused variables** [reportUnusedVariable]: Remove variables that are assigned but never read.
- **Unused return values are intentional for side-effect functions** [reportUnusedCallResult]: Disabled — `logger.info()`, `list.append()` etc. return values that are intentionally discarded.
- **No duplicate imports** [reportDuplicateImport]: Same module imported twice.
- **Getter/setter type must match** [reportPropertyTypeMismatch]: Property getter and setter must have compatible types.
- **__init__ and __new__ signatures must be consistent** [reportInconsistentConstructor]: Prevents confusing constructor overloads.
- **Function parameter types from 3rd-party callbacks may be Unknown** [reportUnknownParameterType]: Warning — propagation from untyped libraries.
- **Lambda types from 3rd-party may be Unknown** [reportUnknownLambdaType]: Warning — type inference from untyped libs.
- **Third-party libs without type stubs** [reportMissingTypeStubs]: Warning — many deps lack py.typed or stubs.

---

# TypeScript / Node.js Rules

## 1. The `any` Type Is Banned (CRITICAL) [no-explicit-any, no-unsafe-assignment, no-unsafe-call, no-unsafe-member-access, no-unsafe-return, no-unsafe-argument]

### 1.1 Never use `any` type — not in declarations, params, returns, or assertions
```typescript
// BAD
const data: any = response.body;
function process(input: any): any { }
const result = value as any;

// GOOD
const data: ResponseBody = response.body;
function process(input: RequestPayload): ProcessResult { }
```

### 1.2 No unsafe operations from `any`
```typescript
// BAD — no-unsafe-assignment
const name: string = anyValue;  // any → typed

// BAD — no-unsafe-call
anyValue();  // calling unknown function

// BAD — no-unsafe-member-access
anyValue.prop;  // accessing unknown property

// BAD — no-unsafe-return
function getName(): string { return anyValue; }

// BAD — no-unsafe-argument
typedFunction(anyValue);  // bypasses type checking
```

## 2. Explicit Types (CRITICAL) [explicit-function-return-type, explicit-module-boundary-types, noImplicitAny]

### 2.1 All functions must have explicit return types
```typescript
// BAD
function getUser(id: string) {
  return db.findUser(id);
}

// GOOD
function getUser(id: string): Promise<User | null> {
  return db.findUser(id);
}
```

### 2.2 All exported functions must have explicit param and return types
### 2.3 Explicit type annotations are preferred even when inferable [no-inferrable-types OFF]
```typescript
// ENCOURAGED — explicit even though inferable
const count: number = 0;
const name: string = "alice";
```

## 3. Promise / Async Safety (CRITICAL) [no-floating-promises, no-misused-promises, await-thenable, require-await]

### 3.1 No floating promises — every Promise must be handled [no-floating-promises]
```typescript
// BAD — rejection silently swallowed
fetchData();
doAsync().then(process);  // no catch

// GOOD
await fetchData();
void fetchData();  // explicitly ignored
fetchData().catch(handleError);
```

### 3.2 No misused promises [no-misused-promises]
```typescript
// BAD — forEach doesn't await, all run in parallel, errors lost
items.forEach(async (item) => { await process(item); });

// BAD — condition always truthy (Promise is an object)
if (asyncFunction()) { }

// GOOD
for (const item of items) { await process(item); }
if (await asyncFunction()) { }
```

### 3.3 Do not await non-thenables [await-thenable]
```typescript
// BAD — indicates wrong variable
await 42;
await nonPromiseValue;
```

### 3.4 async functions must contain await [require-await]
```typescript
// BAD — async keyword is misleading
async function getName(): Promise<string> { return "alice"; }

// GOOD
function getName(): string { return "alice"; }
```

## 4. Switch Exhaustiveness (CRITICAL) [switch-exhaustiveness-check, noFallthroughCasesInSwitch]

### 4.1 All cases must be handled
```typescript
// BAD
switch (status) {
  case "active": return handleActive();
  case "inactive": return handleInactive();
  // missing "pending" case
}

// GOOD
switch (status) {
  case "active": return handleActive();
  case "inactive": return handleInactive();
  case "pending": return handlePending();
  default: {
    const _exhaustive: never = status;
    throw new Error(`Unhandled: ${String(_exhaustive)}`);
  }
}
```

### 4.2 Every switch case must have break/return [noFallthroughCasesInSwitch]
```typescript
// BAD — falls through to next case
switch (x) {
  case 1:
    doA();  // no break!
  case 2:
    doB();
}
```

## 5. Null Safety (CRITICAL) [strictNullChecks, noUncheckedIndexedAccess, no-non-null-assertion, prefer-nullish-coalescing, prefer-optional-chain]

### 5.1 No non-null assertions (!) [no-non-null-assertion]
```typescript
// BAD — removes safety
const name = user!.name;

// GOOD
const name = user?.name ?? "unknown";
if (user) { const name = user.name; }
```

### 5.2 Use ?? instead of || for defaults [prefer-nullish-coalescing]
```typescript
// BAD — || triggers on "", 0, false (valid values)
const count = input || 10;

// GOOD — ?? only triggers on null/undefined
const count = input ?? 10;
```

### 5.3 Use optional chaining [prefer-optional-chain]
```typescript
// BAD
user && user.address && user.address.city

// GOOD
user?.address?.city
```

### 5.4 Indexed access may be undefined [noUncheckedIndexedAccess]
```typescript
// BAD — arr[0] is T | undefined, not T
const first = arr[0];
first.toUpperCase();  // might crash

// GOOD
const first = arr[0];
if (first !== undefined) {
  first.toUpperCase();
}
```

## 6. Boolean Strictness (HIGH) [strict-boolean-expressions]

No truthy/falsy implicit coercion. Use explicit comparisons like Go.
```typescript
// BAD — "" is falsy but valid, 0 is falsy but valid
if (str) { }
if (count) { }

// GOOD
if (str !== "") { }
if (count !== 0) { }
if (obj != null) { }
if (flag === true) { }
```

## 7. Type Assertions (HIGH) [consistent-type-assertions, no-unnecessary-type-assertion]

### 7.1 No object literal assertions — hides missing properties
```typescript
// BAD — all properties are undefined but TS trusts them
const user = {} as User;

// GOOD
const user: User = { name: "alice", email: "alice@example.com" };
```

### 7.2 No unnecessary type assertions [no-unnecessary-type-assertion]
```typescript
// BAD — x is already string
const y = x as string;
```

## 8. Template Literal & String Safety (HIGH) [restrict-template-expressions, restrict-plus-operands, no-base-to-string]

### 8.1 Do not embed objects in template literals
```typescript
// BAD — becomes "[object Object]"
logger.info(`User: ${userObj}`);

// GOOD
logger.info(`User: ${userObj.name}`);
logger.info(`User: ${JSON.stringify(userObj)}`);
```

### 8.2 No implicit type coercion with +
```typescript
// BAD — "5" + 3 = "53" not 8
const result = strValue + numValue;

// GOOD
const result = Number(strValue) + numValue;
```

## 9. Override Safety (HIGH) [noImplicitOverride]

```typescript
// BAD
class Child extends Parent {
  process(): void { }  // silently overrides

// GOOD
class Child extends Parent {
  override process(): void { }
}
```

## 10. Class & Constructor Safety (HIGH) [strictPropertyInitialization, strictBindCallApply, strictFunctionTypes]

### 10.0 All functions with return type must return on all code paths [noImplicitReturns]
```typescript
// BAD — missing return when found is false
function getName(found: boolean): string {
  if (found) {
    return "alice";
  }
  // falls off end → returns undefined
}

// GOOD
function getName(found: boolean): string {
  if (found) {
    return "alice";
  }
  return "";
}
```

### 10.1 All class properties must be initialized in constructor or at declaration
```typescript
// BAD
class Service {
  private db: Database;  // never initialized → undefined
}

// GOOD
class Service {
  private db: Database;
  constructor(db: Database) {
    this.db = db;
  }
}
```

### 10.2 bind/call/apply arguments must match function signature [strictBindCallApply]
### 10.3 Function type assignments must be compatible [strictFunctionTypes]

## 11. Catch Variable Safety (HIGH) [useUnknownInCatchVariables, only-throw-error]

### 11.1 Catch variables are `unknown`, not `any`
```typescript
// BAD — e is unknown, .message may not exist
catch (e) {
  console.log(e.message);
}

// GOOD
catch (e: unknown) {
  if (e instanceof Error) {
    console.log(e.message);
  }
}
```

### 11.2 Only throw Error objects [only-throw-error]
```typescript
// BAD
throw "something went wrong";
throw 404;

// GOOD
throw new Error("something went wrong");
throw new HttpError(404, "Not found");
```

## 12. Void Expression & Unnecessary Code (MEDIUM) [no-confusing-void-expression, no-unnecessary-condition, no-unnecessary-type-parameters, no-unnecessary-boolean-literal-compare, no-unnecessary-qualifier, no-unnecessary-template-expression, noUnusedLocals, noUnusedParameters, allowUnreachableCode]

### 12.0 Do not return void expressions [no-confusing-void-expression]
```typescript
// BAD — looks like it returns a value but returns undefined
return setState(newState);  // setState returns void

// GOOD
setState(newState);
return;
```

```typescript
// BAD [no-unnecessary-condition] — x can never be null
if (x !== null) { }  // when x: string (not nullable)

// BAD [noUnusedLocals]
const unused = 42;  // declared but never read

// BAD [noUnusedParameters]
function process(data: Data, unused: Config): void { }

// BAD — unreachable code
function f(): string {
  return "done";
  cleanup();  // never runs
}
```

## 13. Enum & Type Safety (MEDIUM) [no-duplicate-enum-values, no-mixed-enums, prefer-literal-enum-member, no-invalid-void-type, no-redundant-type-constituents, no-duplicate-type-constituents]

```typescript
// BAD — duplicate enum values
enum Status { Active = 1, Enabled = 1 }

// BAD — mixing string and number enum members
enum Status { Active = 1, Pending = "pending" }

// BAD — redundant type
type T = string | string;  // duplicate
type T = string | never;  // never is redundant
```

## 14. Import & Module Patterns (LOW) [no-var-requires, prefer-as-const, no-this-alias, no-extra-non-null-assertion, method-signature-style]

```typescript
// BAD — use import instead
const fs = require("fs");

// GOOD
import * as fs from "fs";
```

## 15. Array & String Best Practices (MEDIUM) [prefer-for-of, prefer-includes, prefer-string-starts-ends-with, require-array-sort-compare]

```typescript
// BAD — use for...of instead of indexed loop
for (let i = 0; i < arr.length; i++) { arr[i]; }

// BAD — use includes
arr.indexOf(value) !== -1

// GOOD
for (const item of arr) { item; }
arr.includes(value)

// BAD — sort without compare (converts to string)
numbers.sort();

// GOOD
numbers.sort((a, b) => a - b);
```

## 16. inversify DI Exception [no-extraneous-class OFF]

Classes decorated with `@injectable()` are exempt from the "no unnecessary classes" rule. All other classes should have a clear purpose.

---

# No Magic Strings (HIGH)

**String literals used for comparisons, status values, roles, event names, error codes, or configuration keys MUST be defined as constants, enums, or typed literals — not hardcoded inline.** This prevents typos, enables refactoring, and provides autocomplete.

## What to flag

Flag any string literal that:
- Is used in a comparison (`===`, `==`, `!==`, `!=`, `switch/case`, `match/case`)
- Represents a status, role, event name, error code, action type, or category
- Appears more than once across the codebase
- Is used as a dictionary/object key for accessing structured data

## What NOT to flag

Do NOT flag string literals that are:
- Log messages (`logger.info("Processing request")`)
- Error messages in exceptions (`throw new Error("Failed to connect")`, `raise ValueError("Invalid input")`)
- Template strings for user-facing text
- File paths, URLs, or format strings
- Single-use descriptive strings that are not compared against
- Test assertions and fixtures
- Import paths

## Python — Use Enum or Literal

```python
# BAD — magic strings in comparisons and returns
if user.role == "admin":
    grant_access()
if status == "active":
    process()
return {"status": "success", "code": "USER_CREATED"}

# GOOD — Enum for a known set of values
from enum import Enum

class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"

class Status(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"

if user.role == UserRole.ADMIN:
    grant_access()
if status == Status.ACTIVE:
    process()

# GOOD — Literal type for function params
from typing import Literal

def set_mode(mode: Literal["read", "write", "append"]) -> None:
    ...

# GOOD — Constants for error/event codes
class EventCode:
    USER_CREATED = "USER_CREATED"
    USER_DELETED = "USER_DELETED"

return {"status": "success", "code": EventCode.USER_CREATED}
```

## TypeScript — Use const objects, enums, or string unions

```typescript
// BAD — magic strings scattered in code
if (user.role === "admin") { ... }
if (status === "active") { ... }
res.json({ status: "success", code: "USER_CREATED" });
switch (action) {
  case "create": ...
  case "delete": ...
}

// GOOD — const object with derived type
const UserRole = {
  ADMIN: "admin",
  USER: "user",
  GUEST: "guest",
} as const;
type UserRole = (typeof UserRole)[keyof typeof UserRole];

const ResponseStatus = {
  SUCCESS: "success",
  ERROR: "error",
} as const;

const EventCode = {
  USER_CREATED: "USER_CREATED",
  USER_DELETED: "USER_DELETED",
} as const;

if (user.role === UserRole.ADMIN) { ... }
res.json({ status: ResponseStatus.SUCCESS, code: EventCode.USER_CREATED });

// GOOD — string union for function params (enforced at compile time)
type Action = "create" | "update" | "delete";
function handleAction(action: Action): void {
  switch (action) {
    case "create": ...  // exhaustiveness checked by TypeScript
    case "update": ...
    case "delete": ...
  }
}

// GOOD — enum (when you need runtime values + iteration)
enum HttpMethod {
  GET = "GET",
  POST = "POST",
  PUT = "PUT",
  DELETE = "DELETE",
}
```

---

# Request/Response Object Typing (BLOCKER)

**Plain `dict` (Python) and inline objects / `Record<string, any>` (Node.js) are BANNED for API request and response types.** Use Pydantic models (Python) or TypeScript interfaces (Node.js). This is a BLOCKER — flag it on every PR.

## Why This Is a Blocker

Plain dicts disable all compile-time validation:
- No checking that required keys exist
- No checking value types per key
- Typos in key names pass silently (`data["naem"]` vs `data["name"]`)
- No IDE autocomplete or refactoring support
- No automatic request validation (FastAPI) or response shape guarantees

## Python — Use Pydantic BaseModel, Not dict

### BLOCKER: API endpoint parameters must NOT use `dict`
```python
# BAD — dict params bypass all validation
@router.post("/search")
async def search(filters: dict) -> dict:
    ...

@router.post("/search")
async def search(filters: Dict[str, Any]) -> Dict[str, Any]:
    ...

@router.post("/config")
async def update_config(model_config: dict = Body(...)):
    ...

# GOOD — Pydantic models validate shape, types, required fields automatically
class SearchFilters(BaseModel):
    query: str
    limit: int = 20
    sources: list[str] = []
    date_range: DateRange | None = None

class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    took_ms: float

@router.post("/search", response_model=SearchResponse)
async def search(filters: SearchFilters) -> SearchResponse:
    ...
```

### BLOCKER: API response types must NOT use `dict`
```python
# BAD — response shape is invisible to callers and docs
@router.get("/health")
async def health() -> dict:
    return {"status": "healthy", "timestamp": get_timestamp()}

# GOOD — response is documented, validated, and typed
class HealthResponse(BaseModel):
    status: str
    timestamp: int

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="healthy", timestamp=get_timestamp())
```

### BLOCKER: Middleware and helper functions returning user/auth data must NOT use `dict`
```python
# BAD — callers have no idea what keys exist
async def isJwtTokenValid(request: Request) -> dict:
    return {"user_id": uid, "org_id": oid, "email": email}

# GOOD
class AuthContext(BaseModel):
    user_id: str
    org_id: str
    email: str

async def isJwtTokenValid(request: Request) -> AuthContext:
    return AuthContext(user_id=uid, org_id=oid, email=email)
```

### BLOCKER: Internal service method params/returns must NOT use `dict` for structured data
```python
# BAD — connector validation returns unknown structure
def _validate_connector(data: dict) -> Dict[str, Any]:
    ...

# GOOD — typed input and output
class ConnectorValidation(BaseModel):
    is_valid: bool
    connector_type: str
    errors: list[str] = []

def _validate_connector(data: ConnectorConfig) -> ConnectorValidation:
    ...
```

### Acceptable uses of `dict` in Python
- **Logging context**: `logger.info("msg", extra={"key": "val"})` — ok
- **JSON serialization intermediary**: `json.dumps(model.dict())` — ok
- **3rd-party API raw responses** before parsing: `raw: dict[str, Any] = await client.get(...)` — ok, but parse into a model immediately
- **TypedDict for lightweight cases** where Pydantic is overkill (internal helpers, not API boundaries):
  ```python
  class CacheEntry(TypedDict):
      value: str
      expires_at: int
  ```

## Node.js — Use TypeScript Interfaces, Not Inline Objects

### BLOCKER: Controller/route handler request bodies must be typed
```typescript
// BAD — req.body is unknown/any, no validation
async function createUser(req: Request, res: Response): Promise<void> {
  const { name, email } = req.body;  // no type safety
  ...
}

// BAD — Record<string, any> is dict equivalent
async function handleToken(
  decodedToken: Record<string, any>,
): Promise<void> {
  const userId = decodedToken.userId;  // no type checking
}

// GOOD — typed interface for request body
interface CreateUserRequest {
  name: string;
  email: string;
  role: UserRole;
}

async function createUser(
  req: Request<unknown, unknown, CreateUserRequest>,
  res: Response,
): Promise<void> {
  const { name, email, role } = req.body;  // fully typed
}

// GOOD — typed interface for JWT token
interface DecodedToken {
  userId: string;
  orgId: string;
  email: string;
  exp: number;
}

async function handleToken(decodedToken: DecodedToken): Promise<void> {
  const userId: string = decodedToken.userId;  // typed
}
```

### BLOCKER: Response objects must match a typed interface
```typescript
// BAD — inline response, shape unknown to callers
res.status(200).json({
  message: 'Success',
  data: result,
  meta: { requestId, timestamp: Date.now() },
});

// GOOD — typed response interface
interface ApiResponse<T> {
  message: string;
  data: T;
  meta: {
    requestId: string;
    timestamp: number;
  };
}

const response: ApiResponse<UserData> = {
  message: 'Success',
  data: result,
  meta: { requestId, timestamp: Date.now() },
};
res.status(200).json(response);
```

### BLOCKER: Service method params/returns must NOT use `Record<string, any>` or `object`
```typescript
// BAD
async function processData(config: Record<string, any>): Promise<object> { }

// GOOD
interface ProcessConfig {
  timeout: number;
  retries: number;
  format: OutputFormat;
}
interface ProcessResult {
  success: boolean;
  outputPath: string;
}
async function processData(config: ProcessConfig): Promise<ProcessResult> { }
```

### Acceptable uses of plain objects in Node.js
- **Logging metadata**: `logger.info("msg", { requestId, userId })` — ok
- **Spread into typed objects**: `const full: User = { ...partial, createdAt: new Date() }` — ok if `full` has a type
- **Test fixtures**: Mock data in tests — ok
- **Express middleware locals**: `res.locals` — typed via declaration merging

## What Existing Linting Already Catches

| Pattern | Caught by | Level |
|---|---|---|
| `Dict[str, Any]` in Python | Ruff `ANN401` (bans `Any`) | Error |
| `Record<string, any>` in Node.js | ESLint `no-explicit-any` | Error |
| Accessing unknown keys on dict | Pyright `reportUnknownMemberType` | Error |
| Accessing `.prop` on `any` value | ESLint `no-unsafe-member-access` | Error |
| Passing dict to typed function | Pyright `reportArgumentType` | Error |
| Returning dict from typed function | Pyright `reportReturnType` | Error |

## What Gemini Must Catch (NOT caught by linting)

These patterns pass linting but are still bad — Gemini must flag them:

| Pattern | Why it's bad |
|---|---|
| `-> dict[str, str]` on endpoint | Keys not validated, no docs generated |
| `data: dict[str, int]` on endpoint param | No required-field validation |
| `res.json({ message: "ok", data: result })` inline | Response shape undocumented, can drift |
| `TypedDict` with many `Any` fields in state | Defeats purpose of typing |
| Helper functions returning `dict` for structured domain data | Callers don't know the shape |

---

# Security Rules (CRITICAL)

- Never commit secrets, API keys, passwords, or tokens to the repository.
- Validate and sanitize all external inputs (user input, API responses, query parameters).
- Use parameterized queries for database operations. Never concatenate user input into queries.
- Do not disable SSL/TLS verification in production code.

---

# Configuration Access (CRITICAL)

- **CRITICAL: In Python services, never use `key_value_store` / `KeyValueStore` / `EncryptedKeyValueStore` directly for reading or writing configuration values.** Always use `configuration_service` / `ConfigurationService` instead. The `ConfigurationService` wraps the key-value store with LRU caching, environment variable fallbacks, and cross-process cache invalidation via Redis Pub/Sub. Bypassing it causes stale caches across services and breaks cache consistency. The only place that should instantiate or interact with `KeyValueStore` directly is the `ConfigurationService` itself and the DI container wiring.
- When reading config that must reflect the latest value (e.g., after an update), use `config_service.get_config(key, use_cache=False)`.
- When writing config, use `config_service.set_config(key, value)` which handles encryption, caching, and cache invalidation publishing automatically.

---

# Error Handling Rules (HIGH)

- Log errors with sufficient context (key identifiers, operation being performed).
- Do not catch exceptions just to re-raise them without adding context.
- In API endpoints, return appropriate HTTP status codes with descriptive error messages.
- Use structured error responses consistently across all endpoints.

---

# Testing Rules (MEDIUM)

- Write tests for new functionality and bug fixes.
- Do not commit code that breaks existing tests.
- Mock external dependencies (databases, APIs, message queues) in unit tests.

---

# OpenAPI Specification (BLOCKER)

The project maintains a single OpenAPI spec at `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`. This spec MUST stay in sync with all API changes. **Treat any mismatch as a blocking issue on the PR.**

## How to detect API changes in the PR diff

Scan the **entire PR diff** (all changed files, not just specific paths) for any of the following code patterns. These indicate that the PR introduces, modifies, or removes API surface area:

**Express / Node.js patterns (TypeScript/JavaScript):**
- `router.get(`, `router.post(`, `router.put(`, `router.patch(`, `router.delete(`, `router.options(`, `router.head(`
- `app.get(`, `app.post(`, `app.put(`, `app.patch(`, `app.delete(`
- `Router()` — new router instantiation
- Route path strings like `'/api/...'`, `'/v1/...'`

**FastAPI / Python patterns:**
- `@router.get(`, `@router.post(`, `@router.put(`, `@router.patch(`, `@router.delete(`
- `@app.get(`, `@app.post(`, `@app.put(`, `@app.patch(`, `@app.delete(`
- `APIRouter(` — new router instantiation
- Pydantic `BaseModel` subclasses used as request/response types in endpoint signatures
- Changes to `status_code=`, `response_model=`, `dependencies=` in route decorators

**General patterns (any language/framework):**
- New or changed HTTP method handlers
- Changes to URL path definitions or route prefixes
- Added, removed, or renamed query/path/header parameters
- Changes to request body validation or response schemas
- Changes to authentication/authorization decorators or middleware applied to routes (e.g., `@authenticate`, `authMiddleware`, `Depends(...)`)
- Changes to response status codes or error response shapes

## When OpenAPI updates are required

If ANY of the above patterns appear in the PR diff as **added or modified lines**, the PR MUST include corresponding updates to `pipeshub-openapi.yaml`. Specifically:

1. **New endpoints**: Any new route handler added anywhere in the codebase.
2. **Removed endpoints**: Any route handler deleted or commented out.
3. **Changed HTTP methods or paths**: Method or URL path of an existing endpoint is modified.
4. **Request body changes**: Fields added, removed, or modified in request body schemas/validators.
5. **Response schema changes**: Fields, status codes, or content types changed in responses.
6. **Query/path/header parameter changes**: Parameters added, removed, renamed, or changed in type/required status.
7. **Authentication/authorization changes**: Auth requirements added or removed from endpoints.
8. **New or modified middleware**: Middleware that changes request/response shape applied to routes.

## Review instructions

- Scan **every file in the PR diff** for the code patterns listed above. Do NOT limit your search to specific directories or file naming conventions — routes can be defined in any file.
- If the PR contains any of these patterns in added/modified lines but does NOT include changes to `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`, flag this as a **BLOCKER**.
- Use this exact comment format so it is clearly visible:

  > **BLOCKER: OpenAPI spec update required**
  >
  > This PR modifies API endpoints/contracts but does not update the OpenAPI specification at `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`.
  >
  > The OpenAPI spec must stay in sync with all API changes. Please update it to reflect the changes introduced in this PR before merging.
  >
  > **Detected API changes in:**
  > _(list every file and the specific route pattern detected, e.g., "`src/modules/auth/routes/userAccount.routes.ts` — added `router.post('/verify-email', ...)`")_

- If the PR DOES update the OpenAPI spec alongside API changes, verify that the spec changes accurately reflect the code changes (correct paths, methods, parameters, schemas, and status codes).
- If the PR only modifies internal logic with no route/contract changes (e.g., fixing a bug inside a handler without changing its signature, inputs, or outputs), no OpenAPI update is needed — do not flag it.
