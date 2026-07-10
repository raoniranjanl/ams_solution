"""
Quick inspection utility for the shared Qdrant vector database - no UI
needed, just prints what's in the collection directly to your terminal.

Usage:
    python -m ingestion.inspect_collection                     # collection stats + sample records
    python -m ingestion.inspect_collection --limit 20           # show more sample points
    python -m ingestion.inspect_collection --search "ETL job failed"   # test a similarity search
"""
import argparse

from common.vector_db import VectorStore
from config import get_settings


def main():
    parser = argparse.ArgumentParser(description="Inspect the shared Qdrant vector database.")
    parser.add_argument("--limit", type=int, default=5, help="Number of sample records / search results to show.")
    parser.add_argument("--search", type=str, default=None, help="Run a similarity search instead of listing samples.")
    args = parser.parse_args()

    settings = get_settings()
    store = VectorStore(settings.qdrant, settings.embedding)

    info = store.client.get_collection(settings.qdrant.collection)
    print(f"Collection : {settings.qdrant.collection}")
    print(f"Points count: {info.points_count}")
    try:
        print(f"Vector size : {info.config.params.vectors.size}")
        print(f"Distance    : {info.config.params.vectors.distance}")
    except Exception:
        pass  # older qdrant-client versions expose this slightly differently
    print()

    if args.search:
        print(f"Top {args.limit} matches for: {args.search!r}\n")
        results = store.search(args.search, top_k=args.limit)
        if not results:
            print("(no results - is the collection empty, or does the query not match anything?)")
        for r in results:
            print(f"- [{r.score:.3f}] {r.number}: {r.short_description[:80]!r} -> assigned to: {r.assignment_group}")
        return

    print(f"Sample of up to {args.limit} stored records:\n")
    points, _ = store.client.scroll(
        collection_name=settings.qdrant.collection, limit=args.limit, with_payload=True, with_vectors=False
    )
    if not points:
        print("(collection is empty - did ingestion run successfully?)")
    for p in points:
        payload = p.payload or {}
        print(f"- {payload.get('number')}: {payload.get('short_description', '')[:80]!r}")
        print(
            f"    CI: {payload.get('cmdb_ci_name')} | "
            f"assignment_group: {payload.get('assignment_group')} | "
            f"source: {payload.get('source')}"
        )
        close_notes = (payload.get("close_notes") or "")[:100]
        if close_notes:
            print(f"    close_notes: {close_notes!r}")
        print()


if __name__ == "__main__":
    main()
