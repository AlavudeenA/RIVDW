"""
Run this script once on any machine that has internet access.
It downloads the embedding model into the local fastembed_cache/ folder.
Then copy the entire rivdw_buildtime/ folder (including fastembed_cache/)
to the target machine — no internet needed there.

Usage:
    python download_models.py
"""

from pathlib import Path

CACHE_DIR = Path(__file__).parent / "fastembed_cache"
MODEL_NAME = "BAAI/bge-small-en-v1.5"


def main() -> None:
    print(f"Downloading model : {MODEL_NAME}")
    print(f"Destination       : {CACHE_DIR.resolve()}")
    print()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(CACHE_DIR))

    # Embed one sentence to force the full model download
    list(model.embed(["Warming up the model."]))

    size_mb = sum(f.stat().st_size for f in CACHE_DIR.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"\nDone. Model cached at: {CACHE_DIR.resolve()}")
    print(f"Cache size: {size_mb:.0f} MB")
    print()
    print("Next steps:")
    print("  1. Zip or copy the entire rivdw_buildtime/ folder to your office machine.")
    print("  2. The app will use the local cache automatically — no download needed.")


if __name__ == "__main__":
    main()
