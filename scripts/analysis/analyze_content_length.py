#!/usr/bin/env python3
"""
Analyze article content lengths to estimate enrichment costs.
"""
import os
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_PATH = DATA_DIR / "archive_index.parquet"

def estimate_tokens(chars):
    """Rough estimate: 1 token ≈ 4 characters for English text."""
    return chars / 4

def calculate_costs(input_tokens, output_tokens, model_name="gemini-2.5-flash-lite"):
    """Calculate cost for different models."""

    # Pricing per 1M tokens
    prices = {
        "gemini-2.5-flash-lite": {"input": 0.015, "output": 0.06},
        "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    }

    if model_name not in prices:
        model_name = "gemini-2.5-flash-lite"

    price = prices[model_name]
    input_cost = (input_tokens / 1_000_000) * price["input"]
    output_cost = (output_tokens / 1_000_000) * price["output"]

    return input_cost + output_cost

def main():
    if not INDEX_PATH.exists():
        print("Index not found. Run build_index.py first.")
        return

    df = pd.read_parquet(INDEX_PATH)

    print("\n📊 Content Length Analysis")
    print("=" * 80)
    print(f"Total articles in archive: {len(df)}")

    # Filter to articles needing enrichment
    def needs_enrichment(row):
        if row.get("content_corrupted") == True:
            return False
        topics = row.get("topics")
        return topics is None or (isinstance(topics, (list, tuple)) and len(topics) == 0)

    candidates = df[df.apply(needs_enrichment, axis=1)]

    print(f"Articles needing enrichment: {len(candidates)}")
    print(f"Already enriched: {len(df) - len(candidates)}")

    # Use ALL articles for length analysis (to get true average)
    print(f"\nAnalyzing ALL {len(df)} articles for content length statistics...")
    all_articles = df

    # Calculate content statistics from ALL articles
    content_lengths = []
    for _, row in all_articles.iterrows():
        snippet = row.get("content_snippet", "")
        # Estimate full content from word count
        word_count = row.get("word_count", 0)
        # Average: 5 chars per word + spaces
        estimated_chars = word_count * 6
        content_lengths.append(estimated_chars)

    if not content_lengths:
        print("No articles to analyze.")
        return

    df_lengths = pd.DataFrame({"content_chars": content_lengths})

    # Statistics
    avg_chars = df_lengths["content_chars"].mean()
    median_chars = df_lengths["content_chars"].median()
    total_chars = df_lengths["content_chars"].sum()

    print(f"\nContent Statistics:")
    print(f"  Average article length: {avg_chars:,.0f} characters")
    print(f"  Median article length: {median_chars:,.0f} characters")
    print(f"  Total characters: {total_chars:,.0f}")

    # Prompt overhead (roughly 400 characters)
    prompt_overhead_per_article = 600
    output_tokens_per_article = 250  # Estimated structured output

    # Use candidates count if available, otherwise use all articles for projection
    article_count_for_costing = len(candidates) if len(candidates) > 0 else len(df)

    print(f"\n💰 Cost Comparison")
    if len(candidates) > 0:
        print(f"   (for {len(candidates):,} articles needing enrichment)")
    else:
        print(f"   (projection if enriching all {len(df):,} articles)")
    print("=" * 80)

    # Scenario 1: Current (3,500 chars)
    scenario1_input_chars = article_count_for_costing * (3500 + prompt_overhead_per_article)
    scenario1_input_tokens = estimate_tokens(scenario1_input_chars)
    scenario1_output_tokens = article_count_for_costing * output_tokens_per_article
    cost1_lite = calculate_costs(scenario1_input_tokens, scenario1_output_tokens, "gemini-2.5-flash-lite")
    cost1_flash = calculate_costs(scenario1_input_tokens, scenario1_output_tokens, "gemini-2.5-flash")

    print(f"\n1️⃣  Current Setting (3,500 chars per article):")
    print(f"   Input tokens: {scenario1_input_tokens/1_000_000:.2f}M")
    print(f"   Flash-Lite cost: ${cost1_lite:.2f}")
    print(f"   Flash cost: ${cost1_flash:.2f}")

    # Scenario 2: Doubled (7,000 chars)
    scenario2_input_chars = article_count_for_costing * (7000 + prompt_overhead_per_article)
    scenario2_input_tokens = estimate_tokens(scenario2_input_chars)
    scenario2_output_tokens = article_count_for_costing * output_tokens_per_article
    cost2_lite = calculate_costs(scenario2_input_tokens, scenario2_output_tokens, "gemini-2.5-flash-lite")
    cost2_flash = calculate_costs(scenario2_input_tokens, scenario2_output_tokens, "gemini-2.5-flash")

    print(f"\n2️⃣  Doubled (7,000 chars per article):")
    print(f"   Input tokens: {scenario2_input_tokens/1_000_000:.2f}M")
    print(f"   Flash-Lite cost: ${cost2_lite:.2f} (+${cost2_lite-cost1_lite:.2f})")
    print(f"   Flash cost: ${cost2_flash:.2f} (+${cost2_flash-cost1_flash:.2f})")

    # Scenario 3: Full content (use actual average from all articles)
    # For candidates needing enrichment, calculate their actual total
    if len(candidates) > 0:
        candidates_chars = []
        for _, row in candidates.iterrows():
            word_count = row.get("word_count", 0)
            estimated_chars = word_count * 6
            candidates_chars.append(estimated_chars)
        total_candidates_chars = sum(candidates_chars)
        avg_candidate_chars = total_candidates_chars / len(candidates)
    else:
        # Use overall average for projection
        total_candidates_chars = int(avg_chars * article_count_for_costing)
        avg_candidate_chars = avg_chars

    scenario3_input_chars = total_candidates_chars + (article_count_for_costing * prompt_overhead_per_article)
    scenario3_input_tokens = estimate_tokens(scenario3_input_chars)
    scenario3_output_tokens = len(candidates) * output_tokens_per_article
    cost3_lite = calculate_costs(scenario3_input_tokens, scenario3_output_tokens, "gemini-2.5-flash-lite")
    cost3_flash = calculate_costs(scenario3_input_tokens, scenario3_output_tokens, "gemini-2.5-flash")

    print(f"\n3️⃣  Full Content (avg {avg_candidate_chars:,.0f} chars per article):")
    print(f"   Input tokens: {scenario3_input_tokens/1_000_000:.2f}M")
    print(f"   Flash-Lite cost: ${cost3_lite:.2f} (+${cost3_lite-cost1_lite:.2f})")
    print(f"   Flash cost: ${cost3_flash:.2f} (+${cost3_flash-cost1_flash:.2f})")

    # Scenario 4: Compromise (10,000 chars)
    scenario4_input_chars = article_count_for_costing * (10000 + prompt_overhead_per_article)
    scenario4_input_tokens = estimate_tokens(scenario4_input_chars)
    scenario4_output_tokens = article_count_for_costing * output_tokens_per_article
    cost4_lite = calculate_costs(scenario4_input_tokens, scenario4_output_tokens, "gemini-2.5-flash-lite")
    cost4_flash = calculate_costs(scenario4_input_tokens, scenario4_output_tokens, "gemini-2.5-flash")

    print(f"\n4️⃣  Compromise (10,000 chars per article):")
    print(f"   Input tokens: {scenario4_input_tokens/1_000_000:.2f}M")
    print(f"   Flash-Lite cost: ${cost4_lite:.2f} (+${cost4_lite-cost1_lite:.2f})")
    print(f"   Flash cost: ${cost4_flash:.2f} (+${cost4_flash-cost1_flash:.2f})")

    print(f"\n💡 Recommendation:")
    print(f"   For most articles, 7,000-10,000 chars captures the full context")
    print(f"   With Flash-Lite, even full content is affordable (${cost3_lite:.2f})")
    print(f"   Consider: 10,000 chars = good balance of coverage and cost")
    print()

if __name__ == "__main__":
    main()

