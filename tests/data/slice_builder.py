"""A trivial xorq ExprBuilder fixture: slice the first / last N characters of a text column.

Importing this module registers the ``"text_slice"`` TagHandler, so a tagged expression
round-trips through the catalog: catalog it, recover the builder with ``expr.ls.builder``,
switch the option, and build a new expression.

Usage (one process — import registers the handler, so build / recover / rebuild all see it):

    import slice_builder as sb
    import xorq.api as xo
    from xorq.catalog.catalog import Catalog

    products = xo.deferred_read_csv("/abs/products.csv")
    first3 = sb.TextSlice(products, "product_id", which="first", n=3).build()  # tagged expr

    cat = Catalog.from_repo_path("./cat", init=True)
    cat.add(first3, aliases=("pid-first3",))                                   # kind: expr_builder

    builder = cat.get_catalog_entry("pid-first3", maybe_alias=True).expr.ls.builder  # recover
    last3 = builder.with_which("last").build()                                 # the OTHER option
    cat.add(last3, aliases=("pid-last3",))
"""

from __future__ import annotations

from xorq.expr.builders import TagHandler, register_tag_handler

TAG = "text_slice"


class TextSlice:
    """Slice ``which`` ('first' or 'last') ``n`` characters of ``column`` on ``table``."""

    def __init__(self, table: object, column: str, which: str = "first", n: int = 3) -> None:
        self.table = table
        self.column = column
        self.which = which
        self.n = n

    def build(self) -> object:
        """Return the sliced expression, tagged so it round-trips as an ExprBuilder."""
        col = self.table[self.column]
        sliced = col.substr(0, self.n) if self.which == "first" else col.substr(col.length() - self.n, self.n)
        return self.table.mutate(slice=sliced).tag(
            TAG, column=self.column, which=self.which, n=self.n
        )

    def with_which(self, which: str) -> TextSlice:
        """Return a sibling builder with the other option (e.g. ``"last"``)."""
        return TextSlice(self.table, self.column, which=which, n=self.n)


def _from_tag_node(node: object) -> TextSlice:
    return TextSlice(
        node.parent.to_expr(),
        node.metadata["column"],
        node.metadata["which"],
        node.metadata["n"],
    )


register_tag_handler(
    TagHandler(
        tag_names=(TAG,),
        extract_metadata=lambda node: {
            "type": TAG,
            "which": node.metadata.get("which"),
            "n": node.metadata.get("n"),
            "column": node.metadata.get("column"),
        },
        from_tag_node=_from_tag_node,
    )
)
