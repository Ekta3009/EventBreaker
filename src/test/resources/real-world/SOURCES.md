# Real-world consumers — sources

Files are copied byte-for-byte from the source repository. The only change is one
`// Source: <url @ commit>  License: <license>` line prepended at the top.
Original license headers are kept intact.

| Consumer | Product | Transport | License | Source (pinned commit) |
|---|---|---|---|---|
| `fineract/KafkaRemoteMessageListener.java` | Apache Fineract (core banking) | Kafka (`@KafkaListener`) | Apache-2.0 | [apache/fineract tag 1.15.0](https://github.com/apache/fineract/blob/1.15.0/fineract-provider/src/main/java/org/apache/fineract/infrastructure/springbatch/messagehandler/kafka/KafkaRemoteMessageListener.java) (identical to develop@c5cc48a) |
| `killbill/OverdueListener.java` | Kill Bill (subscription billing) | Kill Bill internal bus (`@Subscribe` + `@AllowConcurrentEvents`) | Apache-2.0 | [killbill/killbill tag killbill-0.25.4](https://github.com/killbill/killbill/blob/killbill-0.25.4/overdue/src/main/java/org/killbill/billing/overdue/listener/OverdueListener.java) (identical to master@3868a5b) |

## Classpaths (`--classpath`)

Pinned to releases so source and dependencies match.

| Consumer | How the classpath is produced |
|---|---|
| killbill | Released jars from Maven Central: `killbill-overdue:0.25.4` → `mvn dependency:build-classpath` (91 jars) |
| fineract | Not published to Maven Central — built from the 1.15.0 source archive with Gradle (`:fineract-provider` runtime classpath) |
