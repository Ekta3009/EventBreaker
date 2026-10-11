package fixtures;

// Analyzer fixture: payload wrapped in ConsumerRecord, framework params around it.
public class WrappedPayloadListener {

    private final AuditStore auditStore;

    public WrappedPayloadListener(AuditStore auditStore) {
        this.auditStore = auditStore;
    }

    @KafkaListener(topics = "audit")
    public void onRecord(@Header("key") String key, ConsumerRecord<String, AuditEvent> record, Acknowledgment ack) {
        auditStore.save(record.value());
        ack.acknowledge();
    }

    @org.springframework.context.event.EventListener
    public void onQualified(SettingsChanged event) {
        auditStore.save(event);
    }
}
