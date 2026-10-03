# Chatbot-Driven C++ Compiler Debugger
An Automated Program Repair (APR) teaching assistant designed to help college students securely diagnose and fix C++ compilation errors using localized AI.

## Proposed Architecture
Our architecture functions as a secure wrapper around the standard GCC compiler.
* **Frontend:** Streamlit-based web UI.
* **Execution Sandbox:** Python subprocess layer with strict timeout constraints.
* **Diagnostic Engine:** Heuristic classification module for raw GCC errors.
* **AI Core:** Localized Llama 3 Large Language Model (LLM).
* **Security Pipeline:** Bi-directional validation system (Pre-scan and Post-scan).

## Methodology / Pipeline
1. **Ingestion & Pre-Scan:** Scans user input for malicious system calls.
2. **Compilation:** Passes code to GCC within a sandboxed timeout.
3. **Classification:** Intercepts compiler errors and classifies them using a rule-based engine.
4. **AI Generation:** LLM receives a constrained prompt to generate a pedagogical fix.
5. **Post-Scan Audit:** Validates the LLM's suggested code before presenting it to the user.

## Evidence-Grounded C++ Reasoning Pipeline

The application features an evidence-grounded analysis engine (`reasoning/`) operating on a **Fact → Hypothesis → Verification** paradigm:

1. **Stage 0: Deterministic Fact Collection**
   - Rich compiler warning facts with notes (`-Wall -Wextra -O1 -std=c++17 -fdiagnostics-format=json`).
   - Tree-sitter AST facts (scopes, declarations, initialization status, reads/writes).
   - Intra-procedural dataflow digest (may-read-unassigned tracking).
   - Demoted semantic hints (low-trust candidate inputs).
2. **Stage 1: Intent & Contract Comprehension** (Structured JSON extraction of algorithmic intent and variable roles).
3. **Stage 2: Grounded Hypothesis Generation** (Every candidate claim must cite explicit `fact_ids`).
4. **Stage 3: Evidence Gate** (Deterministic validation: rejects or downgrades hallucinations, phantom line numbers, and contradictory claims).
5. **Stage 4: Adversarial Refuter** (Generates counterexamples or alternative valid explanations).
6. **Stage 5: Epistemic Tier Policy** (Certainty ceiling bounded strictly by verifiable evidence: `definite_error` > `definite_bug` > `likely_bug` > `possible_issue` > `code_smell` > `optimization` > `style`).
7. **Stage 6: Patch Synthesis** (Minimal, surgical line replacements rather than full-program rewrites).
8. **Stage 7: Verification Ladder**
   - **V0:** Clean edit application.
   - **V1:** Scope boundary check (no edits outside bug site ± slack).
   - **V2:** Recompilation clean check (the bug must be resolved without introducing new errors).
   - **V3:** Dynamic execution test (when test cases/I/O exist).
9. **Stage 8: Performance & Optimization** (Loop-invariant code motion, container pre-allocation, pass-by-reference).
10. **Stage 9: Pedagogical Explanation** (Cites exact compiler facts and verified diffs).

### Profiles & Graceful Degradation
- **Profiles:**
  - `quick`: Fast static fact extraction + gated explanation.
  - `standard` (Default): Full hypothesis generation, refutation, tier ceiling, and verified patch synthesis.
  - `thorough`: Includes optimization passes and in-depth performance analysis.
- **Graceful Degradation:** If Ollama is offline or any LLM stage times out, the deterministic compiler facts and local static diagnostics continue to display in the UI without interruption.

## Semantic Name/Type Diagnostics

The live diagnostics and **Compile & Analyze** results include an optional deterministic check for likely variable-name/data-type mismatches. It uses a lightweight C++ tokenizer/declaration extractor and the versioned `semantic_dictionary.json`; it adds no parser dependency and never executes submitted code. Unknown and ambiguous names are kept quiet or shown at low confidence, and semantic findings never block compilation.

In the Streamlit sidebar, semantic analysis can be disabled and the minimum confidence for compile-result findings can be adjusted. High-confidence local primitive corrections may be displayed after a security scan and GCC syntax-only check. Corrections are suggestions only and are not applied to the editor. Set `SEMANTIC_ANALYSIS_ENABLED=false` to default the feature off.

## UI Themes

Light and dark native widget palettes are configured in `.streamlit/config.toml` and can be switched from Streamlit's built-in menu. Shared surface, diagnostic, focus, and responsive tokens live in `assets/app.css`; `ui_theme.py` injects them and synchronizes the existing Ace editor theme without changing its content.

