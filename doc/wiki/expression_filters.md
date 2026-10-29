# Expression filters

How the backend filters runs, iterations and results by metadata, what the
expression language can express, and what it cannot. Written as the reference
for the UI's run filters (runs page, history global search form) and for the
planned combined "tags + expression" input.

Verified on 2026-10-27 against `bublik/core/run/filter_expression.py` with a
parser probe and a database probe (see "Verifying" at the end).

## 1. Two filter families

Every API that filters by metadata accepts the same two kinds of parameters.

| Family | Parameter shape | Backend entry point | Semantics |
| --- | --- | --- | --- |
| Exact list | `run_metas=a;b=1;c` (history: `tags=`, `labels=`, `branches=`, `revisions=`) | `TestIterationResultQuerySet.filter_by_run_metas` | AND of exact matches. Every item must exist as a meta, otherwise the result is empty. |
| Expression | `tag_expr=a & (b=1 \| !c)` | `filter_by_expression(qs, expr, expr_type)` | Boolean expression over one meta type. |

Both families are applied in sequence on the same queryset, so everything is
ANDed together: list filters AND tag expression AND label expression AND ... .

### Which endpoint takes what

| Endpoint | List params (split on `;`) | Expression params | Meta type of each expression |
| --- | --- | --- | --- |
| `GET /api/v2/runs/` and `/runs/charts/` | `run_metas` (matched against tag, label, revision and branch at once) | `tag_expr`, `label_expr`, `revision_expr`, `branch_expr` | tag, label, revision, branch |
| `GET /api/v2/history/` (all variants) | `tags`, `labels`, `branches`, `revisions` (merged into one `run_metas` list), `test_args` (`name=value`), `verdict` + `verdict_lookup` | `tag_expr`, `label_expr`, `branch_expr`, `rev_expr`, `test_arg_expr`, `verdict_expr` | tag, label, branch, revision, test_argument, verdict |

Note the history revision parameter is `rev_expr` while the runs one is
`revision_expr`.

Where the filters apply:

- Run-level expressions (tag, label, revision, branch) filter the run rows
  (`TestIterationResult` with `test_run IS NULL`) by the metas attached to the
  run itself. On the history page the surviving run ids then scope the
  results.
- `test_arg_expr` filters `TestIteration` rows by their `TestArgument`s
  (name/value pairs). It is the only expression type that does not look at
  `Meta` at all.
- `verdict_expr` filters result rows by `Meta` rows of type `verdict`
  attached to each result.

### UI mapping

| UI | URL param | API param |
| --- | --- | --- |
| Runs page "Metas" box (selected chips) | `runData=a;b=1` | `run_metas` |
| Runs page "Tag expression" search bar | `tagExpr` | `tag_expr` |
| History Tags / Labels / Branches / Revisions badge fields | `runData`, `labels`, `branches`, `revisions` | `tags`, `labels`, `branches`, `revisions` |
| History expression text fields (shown by the arrow toggle next to each badge field) | `tagExpr`, `labelExpr`, `branchExpr`, `revisionExpr`, `testArgExpr`, `verdictExpr` | `tag_expr`, `label_expr`, `branch_expr`, `rev_expr`, `test_arg_expr`, `verdict_expr` |

The UI never parses or validates expressions. The string goes to the backend as
typed. The chips in the runs page "Metas" box are chosen from the metas of the
runs currently loaded, and the chip label `key: value` is sent as `key=value`
(`config.keyValueSubmitDelimiter`).

## 2. How an expression is evaluated

`filter_by_expression` runs three stages.

1. **Parse** (`pyparsing`). The string is parsed into atoms and operators. Each
   distinct atom (e.g. `t3=1`) becomes a `TestRunMeta(name, value, relation)`
   and gets a symbol alias `talias<n>`. Identical atoms share one symbol.
2. **Normalise** (`sympy.to_dnf(simplify=True)`). The boolean formula is
   rewritten to disjunctive normal form: an OR of ANDs of possibly negated
   atoms. `t1 & (t2 | t3)` becomes `(t1 & t2) | (t1 & t3)`.
3. **Apply** (Django ORM).
   - An atom adds one `.filter(Q(...))` call. Chained `.filter()` calls on a
     multi-valued relation each get their own join, so `t1 & t3=1` means "has
     a meta row matching t1 AND has a (possibly different) meta row matching
     t3=1".
   - A negated atom adds `.filter(meta.filter_q(negation=True))`. See section 4
     for what that means.
   - An AND applies its members one after another on the same queryset.
   - An OR evaluates each branch as a separate queryset, **reads all matching
     ids into Python**, unions them and returns
     `TestIterationResult.objects.filter(id__in=[...])`. This also means the
     result of an expression containing `|` is always a `TestIterationResult`
     queryset even if the input was a `TestIteration` queryset
     (`test_arg_expr`). The history code copes with it by only using ids.

Every atom query is scoped by the expression's meta type:
`Q(meta_results__meta__type=expr_type)`. A tag expression never sees labels,
even if a label has the same name (verified: `nightly` as a tag expression
returns nothing when `nightly` is a label).

## 3. Grammar

Common to every type:

- Operators: `!` (NOT, prefix), `&` (AND), `|` (OR), parentheses. Precedence
  is `!` > `&` > `|`, so `a | b & c` is `a | (b & c)` and `a & !b | c` is
  `(a & !b) | c`.
- Whitespace around operators and around `=`/`<`/... is ignored.
- Everything else is a parse error. Parse errors are raised as
  `pyparsing.ParseException`, which no view catches, so the client gets an
  HTTP 500 with no usable message. (The exception text itself says
  `Faied to parse expression string '...'. Expected example: ...`.)

### 3.1 Meta expressions: `tag`, `label`, `revision`, `branch`

Atoms, tried in this order:

| Form | Example | Meaning |
| --- | --- | --- |
| `NAME_REV (=\|!=) hex[+]` | `TE_REV=8af383125f20cc5ecdb8393bf+` | name must be `[A-Z0-9]+_REV`, value is hex digits with an optional trailing `+` |
| `NAME_BRANCH (=\|!=) string` | `TE_BRANCH=master` | name must be `[A-Z0-9]+_BRANCH` |
| `string (=\|!=) string` | `t3=1`, `pci!=15b3`, `a=b/c:d-e_f.g%h+i` | value equality |
| `string (=\|!=) !string` | `a=!b` | value equality against a value that starts with `!` |
| `string (<\|<=\|>\|>=\|=\|!=) number` | `t3 > 1`, `t3>=1.5`, `a=1e3` | comparison, **but see section 4: it is a text comparison** |
| `string` | `medford` | meta with that name exists (any value, including no value) |

`string` is one run of the characters `A-Z a-z 0-9 . _ - / % + :`. That is the
complete list. Consequences:

- No spaces inside a name or a value: `a=b c` and `name with space` are parse
  errors.
- No quotes: `a = "quoted value"` is a parse error (quotes exist only in
  verdict expressions).
- No `#`, `@`, `,`, `;`, `*`, `?`, `(`, `)`, `=`, `<`, `>`, `!`, `&`, `|` in a
  value.
- `<`, `<=`, `>`, `>=` only accept a number on the right: `a > b` is a parse
  error. `number` is `[+-]?\d+(\.\d*)?([eE][+-]?\d+)?`.
- `==` is not an operator.
- Names are case sensitive and matched exactly (no wildcards, no regex, no
  substring).
- The two `_REV`/`_BRANCH` forms exist only to reject non-hex revisions and
  are otherwise the same as `string=string`. A revision such as `TE_REV=ZZZ`
  still parses, because it falls through to the generic `string=string` rule.

### 3.2 Test argument expressions: `test_argument`

Same grammar as above (same `string` charset, same operators), applied to
`TestArgument.name`/`TestArgument.value` of each iteration. A bare `proto`
means "the iteration has an argument named proto".

### 3.3 Verdict expressions: `verdict`

Atoms are different:

| Form | Meaning |
| --- | --- |
| `"text"` | result has a verdict whose value is exactly `text`. Anything except `"` is allowed inside, including spaces. There is no escaping, so a verdict containing `"` cannot be expressed. |
| `None` | result has **no** verdict at all |

Operators are the same (`!`, `&`, `|`, parentheses). `!None` means "has at
least one verdict". `"a" & "b"` means both verdicts are present on the result.

Quirk: `None` is parsed with `pyparsing.Word('None')`, which accepts any word
made of the letters N, o, n, e. `Neon`, `nonee` or `eNo` all parse as `None`.
An unquoted word made of other letters (`timeout`) is a parse error.

## 4. Semantics, verified against a database

The probe created runs with tags `t3=1,other=4`, `t3=4`, `t3=1`, `x` (no t3),
`t3=10`, `t3=9`, `t3=1,t3=4`, a run with label `nightly` plus tag `t3=1`, and
an empty run. Results:

| Expression (type `tag`) | Runs returned | Reading |
| --- | --- | --- |
| `t3` | every run with any `t3` tag | presence, value ignored |
| `t3=4` | `t3=4`, `t3=1,t3=4` | exact value |
| `!t3` | `x`, empty | **absence**: no meta of type tag named t3 |
| `t3!=4`, `!(t3=4)`, `!t3=4` | every run with a `t3` tag whose value is not 4, including `t3=1,t3=4` | all three spellings are identical: "**has** a t3 with another value". A run **without** t3 is **not** returned. |
| `t3 & !(t3=4)` | `t3=1`, `t3=9`, `t3=10`, `nightly` run | `t3=1,other=4` and `t3=1,t3=4` are dropped: inside an AND the negation becomes "no meta of the run has value 4", see rule 4 |
| `other & t3!=4`, `other & !(t3=4)` | **nothing** | the only run with `other` is `t3=1,other=4`, and `other=4` itself trips the "no value 4 anywhere" test |
| `other & !nightly` | `t3=1,other=4` | bare negated name is fine inside an AND |
| `x & !t3` | `x` | |
| `t3>1` | `4`, `9`, `10`, `t3=1,t3=4` | |
| `t3>9` | **nothing** | `'10' > '9'` is false as text |
| `t3>=10` | `4`, `9`, `10`, `t3=1,t3=4` | `'4' >= '10'` is true as text |
| `t3<2` | `1`, `10`, ... | `'10' < '2'` is true as text |
| `x | t3=4` | `x`, `t3=4`, `t3=1,t3=4` | OR is a union of ids |
| `x & !x` | nothing | |
| `t3=1 & t3=4` | `t3=1,t3=4` | AND across two different meta rows of the same run |
| `nightly` (type tag) | nothing | type scoping; as `label` it returns the nightly run |

Key rules to remember:

1. **Comparisons are text comparisons.** `Meta.value` is a `TextField`, the
   ORM emits `"value" > '9'` and Postgres compares strings. They only behave
   numerically for values of equal width. There is no cast.
2. **Negating a comparison is not "NOT (comparison)".** The backend flips the
   operator (`=`→`!=`, `<`→`>=`, ...) and still requires the meta to exist:
   `!(t3=4)` is `t3!=4`, which is "has a t3 that is not 4". Only a bare
   negated name (`!t3`) means absence.
3. **Negation and other metas.** `!t1` compiles to
   `NOT (EXISTS(tag meta on run) AND EXISTS(meta named t1 on run))`. The two
   EXISTS are independent, so a run with any tag plus a *label* named `t1`
   is excluded too. Edge case, but real.
4. **A negated value atom changes meaning inside an AND.** Standing alone,
   `t3!=4` compiles to `name = t3 AND NOT EXISTS(value = 4 on that same
   metaresult row)`: per row, so `t3=1,t3=4` passes through its `t3=1` row
   and `t3=1,other=4` passes. As soon as another atom precedes it on the
   queryset (`other & t3!=4`, or the same expression after DNF reordering),
   Django keys the NOT EXISTS on the run instead: `NOT EXISTS(any metaresult
   of this run with value = 4)`, regardless of name or type. Verified:
   `other & t3!=4` returns nothing although `t3=1,other=4` has `other` and
   `t3=1`. Treat `!=`, and negated `<`/`>`, as unreliable in compound
   expressions until the backend builds the negation as an explicit
   subquery on `(name, value)`.
5. **OR materialises ids.** Each OR branch runs as its own query and the ids
   are pulled into Python. Large date ranges with many `|` branches cost
   memory and round trips; AND-only expressions stay a single SQL statement.
6. **Duplicate atoms are shared.** `t1 & t1` is simplified by sympy to `t1`.
   `a & !a` evaluates to an empty queryset.

## 5. Exact list filters, for comparison

`filter_by_run_metas(metas, meta_types)`:

- Input items are `name` or `name=value` (split on the first `=`).
- One `Meta` query finds all items at once with
  `Q(name=..., value=..., type__in=meta_types)`. For `name` without `=` the
  lookup is `value=None`, so a tag that has a value is **not** matched by its
  bare name here (unlike expressions, where a bare name means presence).
- If any item does not resolve to exactly one meta row, the whole result is
  `.none()` (empty page, no error).
- Each found meta adds a `.filter(meta_results__meta=meta)`, i.e. AND.

Runs page: `run_metas` is matched against types tag, label, revision and
branch together. History: `tags`, `labels`, `branches`, `revisions` are
concatenated into one list and matched the same way (the field you put a
value in does not restrict its type).

`test_args` on history is `name=value;name=value`, exact, ANDed. `verdict`
with `verdict_lookup=string` is a list of exact verdict strings (OR via
`__in`), `regex` is a single case-insensitive regex, `none` with an empty
`verdict` means "no verdicts".

## 6. What this means for a combined "tags + expression" input

Facts the spec can rely on:

- A chip that is a plain tag (`medford`, `pci=15b3`) can be sent either as a
  `run_metas` item or as a `tag_expr` atom. They differ in two ways: the
  expression form is scoped to one type and treats a bare name as presence,
  while the list form spans four types and requires the bare name to have no
  value. The expression form is the more predictable one.
- `run_metas` can only express AND. Anything with OR or NOT must go through
  `*_expr`.
- One expression string is one meta type. A tag and a label cannot be mixed in
  one `|`. A combined input that mixes types must emit several `*_expr`
  params, and those are ANDed by the backend. OR across types is impossible
  today.
- Several chips can be joined client side into one expression with `&`
  (`chip1 & chip2 & (expr)`), as long as every chip is a valid atom. That
  gives one param, `tag_expr`, and the backend semantics of section 4.
- Client-side validation that mirrors the grammar: atom
  `^[A-Za-z0-9._\-/%+:]+(\s*(=|!=)\s*!?[A-Za-z0-9._\-/%+:]+|\s*(<=|>=|<|>)\s*[+-]?\d+(\.\d*)?([eE][+-]?\d+)?)?$`,
  joined by `&`, `|`, `!` and parentheses. Anything with a space inside a
  value, quotes, `==`, or a non-numeric right side of `<`/`>` will produce an
  HTTP 500, so the UI should reject it before sending.
- Autocomplete sources: tag names and `name=value` pairs. Values with
  characters outside the charset (spaces, `#`, `,`) exist in real data and
  cannot be used in an expression at all. They can still go through
  `run_metas`, whose only delimiter is `;`.
- Comparison operators are rarely what the user wants (text compare), so
  the input should not advertise `<`/`>` as numeric unless the backend adds a
  cast.
- Verdict and test argument expressions have their own fields on the history
  page and are not part of the runs page; the runs page only has
  `tag_expr`.

## 7. Backend gaps worth fixing alongside the new input

- Parse errors surface as HTTP 500. Catching `pyparsing.ParseException` in
  `filter_by_expression` and raising a DRF `ValidationError` would give the
  UI a 400 with the message and the expected example.
- Numeric comparison needs a cast (or a `::numeric` annotation guarded by a
  regex on `value`).
- Negated value atoms should be built as one subquery on `(type, name,
  value)` so that `t3!=4` means the same thing alone and inside an AND
  (section 4, rule 4). The existing `test_filter_tags` cases pass by luck of
  the fixture: no run there carries the negated value under another name.
- The grammar has no quoting outside verdicts; values with spaces are
  unreachable.
- `None` in verdict expressions should be `pp.Keyword('None')`, not
  `pp.Word('None')`.
- The OR branch materialises ids; a `Q` union or `.union()` would keep it in
  SQL.

## 8. Where the code is

| What | File |
| --- | --- |
| Grammar, DNF, Q building | `bublik/core/run/filter_expression.py` (`TestRunMeta.filter_q`, `TestRunMetasGroup.expr_str_to_dnf`, `apply_filters`) |
| Exact list filter | `bublik/data/managers/result.py` `filter_by_run_metas`, `bublik/data/managers/utils.py` `create_metas_query` |
| Runs endpoint wiring | `bublik/core/run/services.py` `RunService.list_runs_queryset`, `bublik/interfaces/api_v2/run/views.py`, `serializers.py` `RunListQuerySerializer` |
| History endpoint wiring | `bublik/core/history/services.py` `_apply_run_filters`, `_apply_iteration_filters`, `_apply_result_filters`; `bublik/interfaces/api_v2/history/views.py`, `serializers.py` |
| Legacy tag filter (`get_test_runs`, management commands) | `bublik/core/run/stats.py`, `bublik/core/argparse.py` `parser_type_tags` |
| Existing tests | `bublik/tests/test_filter_tags.py` |
| UI runs page | `libs/bublik/features/runs/src/lib/runs-form/runs-form.component.tsx`, `runs-form.container.tsx`, `libs/services/bublik-api/src/lib/endpoints/runs-endpoints.ts` |
| UI history form | `libs/bublik/features/history/src/lib/history-global-search-form/global-search-form/sections/{run,test,verdict}-section.tsx`, `libs/bublik/features/history/src/lib/slice/history-slice.utils.ts` |

## Verifying

Parser-only checks need Django set up but no database:

```bash
cd bublik && (set -a; source ../.env; set +a; .venv/bin/python -c "
import django, os; os.environ['DJANGO_SETTINGS_MODULE']='bublik.settings'; django.setup()
from bublik.core.run.filter_expression import TestRunMetasGroup
mg = TestRunMetasGroup(); print(mg.expr_str_to_dnf('t1 & (t2 | !t3=4)', 'tag'), {m.alias: str(m) for m in mg.metas})
")
```

Database semantics: the existing `bublik.tests.test_filter_tags` covers the
cases in section 4 except the text comparison and the type scoping; run it
with

```bash
cd bublik && (set -a; source ../.env; set +a; .venv/bin/python manage.py test bublik.tests.test_filter_tags --noinput)
```
