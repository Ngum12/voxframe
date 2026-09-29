# Choosing an embedding model

Voxframe matches scenes to images using a CLIP model that encodes both text and
images into one vector space. Two are available.

## The choice

| | `default` | `lite` |
|---|---|---|
| Model | LAION XLM-RoBERTa ViT-B-32 | LAION ViT-B-32 (laion2b) |
| Download | **1.5 GB** | **605 MB** |
| English top-1 | 78% | 76% |
| **French top-1** | **80%** | **68%** |
| English top-3 | 92% | 84% |
| French top-3 | 88% | 80% |
| License | MIT | MIT |

**`default` is recommended.** Use `lite` if your connection is slow, metered, or
you are working offline from a constrained environment — and your content is
primarily English.

The English difference is small (2 points). **The French difference is not**: at
68% top-1, roughly a third of French scenes get the wrong lead image, against a
fifth with `default`.

### Where these numbers come from

A 400-image evaluation with 50 queries per language, run with
`scripts/run_large_eval.py`. The images are **generated** rather than
photographic, so treat these as a comparison between the two models rather than
a prediction of what you will see on your own library. See `DECISIONS.md` D-043
for the biases this introduces.

## Selecting a model

By flag:

```bash
voxframe ingest ./images --author "Name" --license CC0-1.0 --embed-model lite
```

Or in `.env`, to apply everywhere:

```bash
VOXFRAME_EMBED_MODEL=lite
```

## Switching models: use `reembed`

**Vectors from different models are not comparable.** Each model arranges its
vector space differently, so a query embedded by one and compared against images
embedded by another returns rankings that look plausible and mean nothing.

Voxframe will not let that happen. Every stored vector records which model
produced it, and a search across a mismatch fails:

```
This library was embedded with a different model.
  configured: ViT-B-32/laion2b_s34b_b79k
  in library: xlm-roberta-base-ViT-B-32/laion5b_s13b_b90k

Vectors from different models are not comparable, so searching across them
would return meaningless results.

Re-embed the library with the configured model:
  voxframe reembed
```

### When you need to re-embed

- **You changed `--embed-model` or `VOXFRAME_EMBED_MODEL`.** The commonest case.
- **You started with `lite` and want better French.** Switch the setting, then
  re-embed.
- **A Voxframe upgrade changed the default model.** The release notes will say
  so; the mismatch error will also tell you.

### When you do *not* need to re-embed

- Adding new images to a library. Ingest uses the configured model, and
  `library-info` warns if that would create a mixed library.
- Rendering, editing scene plans, or changing style templates. None of those
  touch embeddings.

### Running it

```bash
voxframe reembed --library ./library
```

It recomputes only the stale vectors, so re-running after an interruption
resumes rather than starting over. Expect roughly **40 ms per image** on CPU —
about 40 seconds per thousand images.

Files that have moved or been deleted since ingest are reported as failures and
left in the database rather than removed: losing a record because a path broke
is your decision, not the tool's.

### Checking what a library uses

```bash
voxframe library-info --library ./library
```

```
20 assets in library
  License    Assets
  CC0-1.0        20
  Embeddings: xlm-roberta-base-ViT-B-32/laion5b_s13b_b90k
```

A library showing **two** models is mid-migration. Run `voxframe reembed` to
finish it; search will refuse until you do.

## Why not a third option

Both models here are MIT-licensed with text and image towers trained together,
so each is internally consistent by construction.

A distilled multilingual text encoder (`clip-ViT-B-32-multilingual-v1`) was
evaluated and rejected. It is aligned to **OpenAI's** CLIP image encoder, not
LAION's — pairing it with LAION image weights would combine two unrelated vector
spaces. It also scored lowest of the three, and OpenAI's model card places any
deployed use out of scope. See `DECISIONS.md` D-034 and D-008.
