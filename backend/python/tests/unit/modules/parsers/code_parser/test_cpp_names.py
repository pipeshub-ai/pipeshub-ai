"""C++ definitions named the way a compiler-grade index names them (oatpp's unmatched-definition patterns).

Each case is a construct a reference index names and the parser did not: in-class destructors and operators, explicit
specializations, nested classes declared as members, pure virtual members, out-of-line static data members;
and a member prototype, which is no symbol (its definition holds the body).
"""
from app.modules.parsers.code_parser.code_file_parser import qualified_name_for
from app.modules.parsers.code_parser.engine import parse_code

SRC = """namespace ns {
class Executor {
 private:
  class SubmissionProcessor : public Worker {
   public:
    void execute() {}
  };
 public:
  virtual ~Executor() = default;
  virtual void onNewItem(int x) = 0;
  Executor(const Executor&) = delete;
  bool operator==(const Executor& o) const { return true; }
  void plain(int);
  int counter;
};
const Status Status::CODE_100(100, "Continue");
int Table::count = 0;
template<> struct hash<ns::Boolean> { int operator()(int v) const { return v; } };
}
"""


def names(source: str = SRC) -> set[str]:
    return {qualified_name_for(s.kind, s.name, s.parent_chain)
            for s in parse_code(source.encode(), "cpp").symbols if s.name}


def test_in_class_destructors_and_operators_are_named() -> None:
    got = names()
    assert {"method:ns.Executor.~Executor", "method:ns.Executor.operator=="} <= got, sorted(got)


def test_an_explicit_specialization_is_named_by_its_template() -> None:
    got = names()
    assert "struct:ns.hash" in got and "method:ns.hash.operator()" in got, sorted(got)
    assert not any("<" in n for n in got)


def test_a_nested_class_declared_as_a_member_is_its_container() -> None:
    got = names()
    assert {"class:ns.Executor.SubmissionProcessor", "method:ns.Executor.SubmissionProcessor.execute"} <= got
    assert "method:ns.Executor.execute" not in got


def test_a_pure_virtual_member_is_a_method_and_a_prototype_is_no_symbol() -> None:
    got = names()
    assert "method:ns.Executor.onNewItem" in got
    assert not any(n.endswith(".plain") for n in got), sorted(got)   # its definition holds the body
    assert "field:ns.Executor.counter" in got                          # a data member is still a field


def test_a_function_pointer_member_is_still_a_field() -> None:
    got = names("struct Ops {\n  void (*on_read)(int fd);\n  int (*cmp)(const void*, const void*);\n};\n")
    assert {"field:Ops.on_read", "field:Ops.cmp"} <= got, sorted(got)


def test_out_of_line_static_members_are_fields_of_their_class() -> None:
    got = names()
    assert {"field:ns.Status.CODE_100", "field:ns.Table.count"} <= got, sorted(got)


def test_operator_names_carry_no_spaces_but_operator_new_keeps_its_own() -> None:
    got = names("struct S {\n  bool operator < (const S&) const { return true; }\n"
                "  void* operator new (unsigned long n) { return 0; }\n};\n"
                "bool S::operator > (const S&) const { return false; }\n"
                "S operator + (const S& a, const S& b) { return a; }\n")
    assert {"method:S.operator<", "method:S.operator>", "method:S.operator new", "function:operator+"} <= got, sorted(got)


def test_branches_inside_a_nested_namespace_or_class_are_members() -> None:
    got = names("namespace a {\nnamespace b {\n#if defined(WIN32)\nvoid C::m(int x) {\n  win();\n}\n#else\n"
                "void C::m(int x) {\n  posix();\n}\n#endif\n}\n}\n"
                "class Outer {\n  class Inner {\n#ifdef FAST\n    void go() {}\n#endif\n  };\n};\n")
    assert {"method:a.b.C.m", "method:Outer.Inner.go"} <= got, sorted(got)


def test_plain_globals_and_c_code_are_unchanged() -> None:
    c = {qualified_name_for(s.kind, s.name, s.parent_chain) for s in parse_code(
        b"int global = 1;\nstruct cfg { int n; };\nvoid run(void);\nvoid run(void) { }\n", "c").symbols if s.name}
    assert "function:run" in c and any(n.endswith(":cfg") for n in c) and not any("global" in n for n in c), c
