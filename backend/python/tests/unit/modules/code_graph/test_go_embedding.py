"""Go struct embedding promotes the embedded type's methods.

`type Engine struct { RouterGroup }` makes `engine.GET(...)` call RouterGroup.GET. Embedding feeds method
lookup only. It is not inheritance in the answer keys, so no
INHERITS edge is written and HERITAGE scoring stays as it was.
"""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value

pytestmark = pytest.mark.asyncio

GROUP = b'''package gin

type RouterGroup struct{}

func (g *RouterGroup) GET(path string) {}

func (g *RouterGroup) Use() {}

type Base struct{}

func (b *Base) Deep() {}

type Middle struct {
	Base
}

func (m *Middle) Use() {}

type X struct{}

func (x *X) Do() {}

type Y struct{}

func (y *Y) Do() {}

type Loop1 struct {
	*Loop2
}

type Loop2 struct {
	Loop1
}
'''

ENGINE = b'''package gin

type Engine struct {
	RouterGroup
	*Middle
	X
	Y
}

func (e *Engine) Run() {}

func serve(e *Engine) {
	e.GET("/")
	e.Run()
	e.Deep()
	e.Use()
	e.Do()
}

func spin(l *Loop1) {
	l.Missing()
}
'''


async def _calls(graph, index_file) -> set[str]:
    await index_file("recG", "gin/group.go", GROUP, "go")
    await index_file("recE", "gin/engine.go", ENGINE, "go")
    await _build(graph)
    return _targets(graph, CALLS, "gin/engine.go")


async def test_an_embedded_types_method_is_promoted(graph, index_file) -> None:
    calls = await _calls(graph, index_file)
    assert "gin/group.go::method:RouterGroup.GET" in calls
    assert "gin/engine.go::method:Engine.Run" in calls  # the type's own method, as before


async def test_promotion_is_transitive_through_a_pointer_embed(graph, index_file) -> None:
    assert "gin/group.go::method:Base.Deep" in await _calls(graph, index_file)


async def test_the_shallower_promotion_wins_and_a_tie_is_ambiguous(graph, index_file) -> None:
    calls = await _calls(graph, index_file)
    # Use: RouterGroup.Use and Middle.Use are both depth 1 -> ambiguous, as the compiler rejects it.
    assert not {"gin/group.go::method:RouterGroup.Use", "gin/group.go::method:Middle.Use"} & calls
    assert not {"gin/group.go::method:X.Do", "gin/group.go::method:Y.Do"} & calls
    assert "gin/group.go::method:Base.Deep" in calls  # one definition two levels down still wins


async def test_an_embedding_cycle_terminates_and_links_nothing(graph, index_file) -> None:
    calls = await _calls(graph, index_file)
    assert not any("Missing" in c for c in calls)
    assert "gin/group.go::method:RouterGroup.GET" in calls  # the build finished and promoted the rest


async def test_embedding_writes_no_heritage_edge(graph, index_file) -> None:
    assert "gin/group.go::method:RouterGroup.GET" in await _calls(graph, index_file)
    for relation in (RecordRelations.INHERITS.value, RecordRelations.IMPLEMENTS.value,
                     RecordRelations.OVERRIDES.value):
        assert graph.code_edges(relation) == []


SHADOW = b'''package gin

type Protocol struct{}

func (p *Protocol) Close() {}

func (p *Protocol) Open() {}

type Conn struct {
	Protocol
}

func (c *Conn) Close() {}

func shut(c *Conn) {
	c.Close()
	c.Open()
}
'''


async def test_a_method_shadowing_a_promoted_one_overrides_nothing(graph, index_file) -> None:
    """An embed is no base: `Conn.Close` hides `Protocol.Close` (the shallower one is called) without an
    OVERRIDES edge, and an embedded type named like an abstract base (`Protocol`) makes no interface."""
    await index_file("recS", "gin/conn.go", SHADOW, "go")
    await _build(graph)
    assert _targets(graph, CALLS, "gin/conn.go") == {"gin/conn.go::method:Conn.Close",
                                                     "gin/conn.go::method:Protocol.Open"}
    for relation in (RecordRelations.INHERITS.value, RecordRelations.IMPLEMENTS.value,
                     RecordRelations.OVERRIDES.value):
        assert graph.code_edges(relation) == []


@pytest.mark.parametrize("language,source", [
    ("go", "package p\n\ntype Engine struct {\n\tBase\n\tgroup RouterGroup\n}\n"),
    ("python", "class Engine(Base):\n    group: RouterGroup\n"),
    ("typescript", "class Engine extends Base {\n  group: RouterGroup;\n}\ninterface I extends Base {}\n"),
    ("java", "class Engine extends Base implements Runner {\n  RouterGroup group;\n}\n"),
])
@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")  # the module's asyncio mark; this one is sync
def test_only_go_emits_embeds(language, source) -> None:
    """Go's embedded type is an "embeds" fact (a named field is not); no other parser emits one, so their
    heritage is as before."""
    from app.modules.parsers.code_parser.engine import parse_code

    facts = parse_code(source.encode(), language).pending
    embeds = [f.to_name for f in facts if f.context == "embeds"]
    if language == "go":
        assert embeds == ["Base"]
        return
    assert not embeds
    assert [f for f in facts if f.to_name == "Base"]  # the real heritage is still there


SERVER = b'''package web

type Server struct{}

func (s *Server) ListenAndServe() {}
'''

OUTSIDE = b'''package api

import "net/http"

type App struct {
	http.Server
}

func run(a *App) {
	a.ListenAndServe()
}
'''


async def test_an_embedded_type_from_an_outside_package_is_no_repo_type_so_named(graph, index_file) -> None:
    """`http.Server` is net/http's, never the repo's own `web.Server`: its methods stay unlinked."""
    await index_file("recW", "web/server.go", SERVER, "go")
    await index_file("recA", "api/app.go", OUTSIDE, "go")
    await _build(graph)
    assert _targets(graph, CALLS, "api/app.go") == set()


GENERIC = b'''package box

type Box[T any] struct{}

func (b *Box[T]) Get() {}

type Holder struct {
	Box[int]
}

func use(h *Holder) {
	h.Get()
}
'''


async def test_a_generic_embedded_type_promotes_its_methods(graph, index_file) -> None:
    await index_file("recB", "box/box.go", GENERIC, "go")
    await _build(graph)
    assert _targets(graph, CALLS, "box/box.go") == {"box/box.go::method:Box.Get"}


def test_a_union_of_types_in_an_interface_is_no_embed() -> None:
    """`interface { A | B }` is a type-set constraint, not an embedded `A`; `interface { A }` embeds A."""
    from app.modules.parsers.code_parser.engine import parse_code

    facts = parse_code(b"package p\n\ntype C interface {\n\tA | B\n}\n\ntype D interface {\n\tA\n}\n", "go").pending
    assert [(f.to_name, f.context) for f in facts if f.context == "embeds"] == [("A", "embeds")]
