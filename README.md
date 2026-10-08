# EventBreaker

**Break your event consumers before production does.**

EventBreaker is an AI-powered reliability analysis and fault-injection tool for Java event-driven applications. Point it at any `@EventBreakerConsumer`-annotated Java class and it automatically identifies reliability risks, proves them with real JVM execution, and generates grounded fix recommendations — all in a single command.

Built for the **Nebius × NVIDIA Global AI Hackathon 2026**.

---

## The Problem

Event-driven systems using Kafka, SQS, RabbitMQ, or similar brokers deliver events **at least once**. That guarantee is one of the most common sources of silent production bugs:

- The same payment gets charged twice because the consumer was never tested for duplicate delivery
- An inventory reservation fires on every retry with no deduplication check
- An order is saved multiple times while a downstream publish silently fails — data is inconsistent and no exception was raised

These bugs are invisible in unit tests, hard to reproduce locally, and expensive in production. EventBreaker finds and proves them before you ship.

---

## How It Works

```
eventbreaker analyze OrderConsumer.java
         │
         ▼
  [Static Analyzer]             JavaParser reads the consumer AST.
  Java JAR → ConsumerAnalysis   Extracts: dependencies, method calls,
                                call order, event type, injection style.
                                No LLM involved — deterministic.
         │
         ▼
  [Agent 1: Risk Identifier]    NVIDIA Nemotron-3-Ultra-550B via
  Nemotron Ultra 550B           Nebius Token Factory reasons about
                                the consumer's specific code and returns
                                risks WITH a structured TestConfig:
                                callPattern + faultInjections[].
                                The LLM decides WHAT to test.
                                It never writes Java.
         │
         ▼
  [Python Feasibility Check]    Pure Python validates TestConfig
  No LLM — deterministic        against ConsumerAnalysis.
                                Flags theoretical risks (no harness
                                support) without an LLM call.
         │
         ▼
  [Executor]                    Assembles a Java harness from
  Parameter-driven assembly     ConsumerAnalysis + TestConfig.
                                All dep/method names come from
                                static analysis — always compiles.
                                Runs in local JVM or Nebius Sandbox.
         │
         ▼
  [Observations]                call counts per dep.method
  Richer signal                 + call sequence (ordering)
                                + consumerThrew (swallowing detection)
         │
         ▼
  [Agent 2: Diagnostician]      NVIDIA Nemotron-3-Super-120B via
  Nemotron Super 120B           Nebius Token Factory diagnoses
                                observed signals — grounded in
                                evidence, not speculation.
         │
         ▼
  [Report]                      Rich terminal output
                                + eventbreaker-report.md
```

---

## Why This Architecture

The key insight: **the LLM should decide what to test, not how to implement the test in Java.**

Previous approaches asked the LLM to generate raw Java code injected into a harness it couldn't see — producing compile errors, wrong variable names, and hallucinated Mockito API calls that required constant prompt patching.

EventBreaker's vocabulary-based architecture separates concerns:

| Concern | Who handles it |
|---|---|
| What risks exist in this consumer? | Nemotron Ultra (reasoning) |
| How to test each risk? | Structured TestConfig vocabulary |
| Translating TestConfig to Java? | Python executor (deterministic) |
| What do the observations mean? | Nemotron Super (diagnosis) |

The TestConfig vocabulary has three **call patterns** and three **fault types** that compose into hundreds of specific test configurations — without the LLM writing a single line of Java.

---

## NVIDIA + Nebius Integration

| Role | Model / Service |
|---|---|
| Risk identification + test planning | NVIDIA Nemotron-3-Ultra-550B via Nebius Token Factory |
| Reliability diagnosis | NVIDIA Nemotron-3-Super-120B via Nebius Token Factory |
| Sandboxed Java execution | Nebius ConTree SDK — isolated Alpine + JDK container |
| Inference API | Nebius Token Factory (`api.tokenfactory.nebius.com`) |

**Why multiple Nemotron tiers?**
Ultra (550B total / 55B active) is used for risk identification — it reasons about unfamiliar consumer code it has never seen and produces structured TestConfig output. Super (120B total / 12B active) handles diagnosis — faster, sufficient for interpreting structured observation data. Nano remains available for any lightweight pre-screening.

This directly follows the Nebius best practice: reach for Ultra when serious reasoning is required, let Super handle the faster everyday calls.

---

## Requirements

- Python 3.12+
- Java 21+ and Maven 3.8+ (on your `PATH`)
- A Nebius API key with access to Nebius Token Factory
- A Nebius Project ID (optional — enables sandboxed execution via Nebius ConTree)

---

## Installation

```bash
git clone https://github.com/Ekta3009/EventBreaker
cd EventBreaker

# Build the Java static analyzer fat JAR
mvn package -q

# Install the Python CLI
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## Configuration

```bash
cp .env.example .env
```

```bash
# .env
NEBIUS_API_KEY=your_nebius_api_key_here

# Optional — runs Java harnesses in an isolated Nebius Sandbox
# instead of the local JVM. Strongly recommended for untrusted consumers.
NEBIUS_PROJECT_ID=your_project_id_here
```

The `.env` file is loaded automatically on every run.

---

## Usage

```bash
eventbreaker analyze path/to/YourConsumer.java
```

### Example

```bash
eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
```

**Pipeline steps:**

1. Static analysis — extracts consumer structure (no LLM, instant)
2. Risk identification — Nemotron Ultra identifies risks + TestConfig for each
3. Feasibility check — Python validates TestConfig against analysis (no LLM, instant)
4. Execution — harness assembled from TestConfig, built with Maven, run in JVM or sandbox
5. Diagnosis — Nemotron Super interprets observations and produces grounded findings
6. Report — saved to `eventbreaker-report.md`

---

## What It Detects

EventBreaker identifies risks specific to each consumer's code — not from a fixed checklist. Example scenarios Nemotron has identified in practice:

| Scenario type | What it proves |
|---|---|
| `IDEMPOTENCY_VIOLATION` | All dep methods called 2× on duplicate delivery — double charging, double publishing |
| `PARTIAL_PROCESSING` | Make dep X throw → observe which downstream deps were skipped entirely |
| `SILENT_DATA_LOSS` | Make a terminal dep throw → check if consumer propagates or swallows |
| `DOWNSTREAM_FAILURE` | Inject fault mid-execution → observe partial commit + skipped steps |
| `RACE_CONDITION` | Two threads deliver simultaneously → observe interleaved execution counts |
| `MISSING_TRANSACTION_BOUNDARY` | Observe call ordering — is begin always followed by commit? |

Risks that require return-value control (null checks, specific values) or real infrastructure (network partitions, OS faults) are correctly classified as **theoretical** with a clear explanation — not silently skipped.

---

## Consumer Compatibility

EventBreaker works with any Java consumer annotated with `@EventBreakerConsumer`. It auto-generates stub classes for unknown types and patches them iteratively on compiler errors.

| Pattern | Supported |
|---|---|
| Constructor injection | ✅ |
| Field injection (Spring `@Autowired` style) | ✅ via reflection |
| Package-private fields | ✅ via `setAccessible` |
| 4+ dependencies | ✅ |
| Generic typed dependencies (`Repository<Product>`) | ✅ |
| Deep event method chains (`event.getOrder().getId()`) | ✅ |
| Conditional logic, early returns | ✅ |
| Safe / idempotent consumers | ✅ correctly reported as no risk |

---

## Project Structure

```
EventBreaker/
├── src/main/java/com/eventbreaker/    Java: @EventBreakerConsumer annotation
│   ├── annotation/                    + JavaParser-based static analyzer
│   └── analyzer/                      fat JAR built by mvn package
├── src/test/resources/consumers/      12 example consumers (safe, risky, adversarial)
├── target/eventbreaker-analyzer.jar   Pre-built static analyzer JAR
├── eventbreaker/                      Python package
│   ├── cli/main.py                    Typer CLI — 6-step pipeline
│   ├── analyzer/bridge.py             Java JAR → Python bridge
│   ├── agent/nemotron.py              Nemotron Ultra risk identification + TestConfig
│   ├── scenarios/
│   │   ├── models.py                  ChaosScenario, TestConfig, FaultInjection
│   │   ├── executor.py                Parameter-driven Java harness assembly + Maven build
│   │   ├── experiment.py              Pure Python feasibility validator
│   │   └── validator.py               Schema validation for LLM output
│   ├── sandbox/executor.py            Nebius ConTree SDK wrapper
│   ├── diagnosis/analyzer.py          Nemotron Super diagnosis
│   ├── report/reporter.py             Markdown report generator
│   └── models/                        Pydantic models
│       ├── consumer.py                ConsumerAnalysis + sub-models
│       └── observation.py             ObservationEntry, ObservationResult, ReliabilityFinding
├── .env.example                       Environment variable template
└── pyproject.toml                     Python package config + dependencies
```

---

## Hackathon Track

**Coding and Agentic Engineering** — a developer tool that uses multiple NVIDIA Nemotron agents to write, execute, and diagnose reliability tests for Java event consumers, powered end-to-end by Nebius Token Factory.
