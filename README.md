# EventBreaker

**Break your event consumers before production does.**

EventBreaker is an AI-powered reliability analysis tool for Java event-driven applications. Point it at any `@EventBreakerConsumer`-annotated Java class and it will automatically identify reliability risks, simulate fault scenarios with real code execution, and generate a fix recommendation — all in a single command.

Built for the **Nebius × NVIDIA Global AI Hackathon 2026**.

---

## The Problem

Event-driven systems using Kafka, SQS, or similar brokers deliver events **at least once**. That guarantee — which sounds safe — is one of the most common sources of bugs in production:

- The same payment gets charged twice because the consumer wasn't idempotent
- An email is sent on every retry because there is no deduplication check
- An order is saved multiple times because duplicate event delivery was never tested

These bugs are invisible in unit tests, hard to reproduce locally, and catastrophic in production.

---

## How It Works

```
eventbreaker analyze OrderConsumer.java
         │
         ▼
  [Static Analysis]          JavaParser extracts dependencies,
  Java AST → JSON            method calls, and event type

         │
         ▼
  [AI Risk Identification]   NVIDIA Nemotron-3-Nano-30B identifies
  Nemotron via Nebius        the highest-severity reliability risks

         │
         ▼
  [Scenario Execution]       EventBreaker generates a Java harness,
  Local JVM or               compiles it, delivers the event twice
  Nebius Sandbox             with mocked dependencies, and counts
                             every call to every service

         │
         ▼
  [AI Diagnosis]             Nemotron explains the observed failure
  Nemotron via Nebius        and suggests a concrete fix

         │
         ▼
  [Report]                   Terminal output + eventbreaker-report.md
```

---

## Powered By

| Component | Technology |
|-----------|-----------|
| Risk identification | NVIDIA Nemotron-3-Nano-30B-A3B via Nebius Token Factory |
| Fault diagnosis | NVIDIA Nemotron-3-Nano-30B-A3B via Nebius Token Factory |
| Sandbox execution | Nebius ConTree SDK — isolated Alpine + JDK container |
| Static analysis | JavaParser (AST-based, language-level) |
| Dependency mocking | Mockito 5 (subclass mock maker, no JVM agent) |

---

## Requirements

- Python 3.12+
- Java 21+ and Maven 3.8+ (on your `PATH`)
- A Nebius API key ([nebius.com](https://nebius.com))
- A Nebius Project ID (for sandbox execution — optional, falls back to local JVM)

---

## Installation

```bash
git clone https://github.com/Ekta3009/EventBreaker
cd EventBreaker

# Build the Java static analyzer
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

Edit `.env`:

```bash
NEBIUS_API_KEY=your_nebius_api_key_here

# Optional — set this to run executions in an isolated Nebius Sandbox
# instead of the local JVM. Recommended for production use.
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

**What happens:**

1. Static analysis extracts the consumer's dependencies and call graph
2. Nemotron identifies 2–4 reliability risks specific to this consumer's code
3. The highest-priority `DUPLICATE_EVENT` scenario is executed — the same event is delivered twice against mocked dependencies
4. Invocation counts for every dependency method are collected and shown
5. Nemotron diagnoses the observed failure and suggests a fix
6. A Markdown report is saved to `eventbreaker-report.md`

---

## Example Output

### Consumer analysed: `OrderConsumer`

```java
@EventBreakerConsumer(eventType = "OrderCreated")
public void consume(OrderCreated event) {
    Order order = orderRepository.get(event.getOrderId());
    paymentClient.charge(order.getId());   // ← no idempotency guard
    order.markPaid();
    orderRepository.save(order);
    eventPublisher.publish(new OrderProcessed(order.getId()));
}
```

### Identified risks (Nemotron)

```
Risk #1  DUPLICATE_EVENT
  Reason:  paymentClient.charge has no idempotency guard — duplicate
           delivery will charge the customer twice.
  Target:  paymentClient.charge
  Concern: Verify payment is not processed more than once per order.

Risk #2  DOWNSTREAM_FAILURE
  ...
```

### Scenario executed: DUPLICATE_EVENT

```
Observations
────────────────────────────────────
paymentClient.charge      2   ← DUPLICATE
orderRepository.get       2   ← DUPLICATE
orderRepository.save      2   ← DUPLICATE
eventPublisher.publish    2   ← DUPLICATE
```

### Reliability finding (Nemotron)

```
Severity:  HIGH
Observed:  paymentClient.charge was called 2 times on duplicate event delivery

Why it matters:
  The consumer charges the customer on every event delivery with no
  idempotency check. At-least-once delivery guarantees this will happen
  in production, resulting in double charges.

Suggested fix:
  Check whether this order has already been charged before calling
  paymentClient.charge, e.g. by storing a processed event ID in a
  deduplication store and returning early if it is already present.
```

---

## Consumer Compatibility

EventBreaker works with any Java consumer annotated with `@EventBreakerConsumer`. It auto-generates stub classes for any types not on the classpath, so it does not require access to your full Maven dependency tree.

Tested patterns:

| Pattern | Example |
|---------|---------|
| Constructor injection | `OrderConsumer(OrderRepository r, PaymentClient p)` |
| Field injection (Spring-style) | `private OrderRepository orderRepository;` |
| Package-private fields | `UserRepository userRepository;` |
| 4+ dependencies | `MultiDepConsumer` |
| Generic typed dependencies | `ProductRepository<Product> productRepository` |
| Deep method chains | `event.getOrder().withCustomer(...)` |
| Conditional logic | `if (event.isPriority()) { ... }` |
| Safe / idempotent consumers | Detected and correctly reported as no-risk |

---

## Project Structure

```
EventBreaker/
├── src/main/java/com/eventbreaker/    Java: @EventBreakerConsumer annotation +
│                                      JavaParser-based static analyzer
├── src/test/resources/consumers/     12 example consumers (safe, risky, adversarial)
├── target/eventbreaker-analyzer.jar  Pre-built fat JAR (mvn package)
├── eventbreaker/                     Python package
│   ├── cli/main.py                   Typer CLI — 5-step pipeline
│   ├── analyzer/bridge.py            Java JAR → Python bridge
│   ├── agent/nemotron.py             Nemotron risk identification
│   ├── scenarios/executor.py         Java harness generation + Maven build + execution
│   ├── sandbox/executor.py           Nebius ConTree SDK wrapper
│   ├── diagnosis/analyzer.py         Nemotron diagnosis
│   ├── report/reporter.py            Markdown report generator
│   └── models/                       Pydantic models (ConsumerAnalysis, ChaosScenario, …)
├── .env.example                      Environment variable template
└── pyproject.toml                    Python package config
```

---

## Scenario Types

| Scenario | Description | Status |
|----------|-------------|--------|
| `DUPLICATE_EVENT` | Same event delivered twice (at-least-once delivery) | Implemented |
| `DOWNSTREAM_FAILURE` | Dependency throws mid-execution | Planned |
| `CRASH_AFTER_SIDE_EFFECT` | JVM crash after a side effect completes | Planned |
| `OUT_OF_ORDER` | Events arrive in the wrong sequence | Planned |

---

## Limitations

- Consumer must be annotated with `@EventBreakerConsumer` (the annotation ships with this tool and is a one-line import)
- Java 21 required for compilation of the generated execution harness
- `DUPLICATE_EVENT` is the only fully executed scenario in this release; the others are identified by Nemotron but not yet run
