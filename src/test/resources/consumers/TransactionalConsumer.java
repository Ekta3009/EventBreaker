package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Real-world pattern — manual transaction demarcation.
 *
 * Moves money between two accounts inside a hand-rolled transaction,
 * then publishes an event after commit. There is no rollback on
 * failure and the publish happens outside the transaction (dual write).
 */
public class TransactionalConsumer {

    private final TransactionManager transactionManager;
    private final LedgerRepository ledgerRepository;
    private final EventPublisher eventPublisher;

    public TransactionalConsumer(
            TransactionManager transactionManager,
            LedgerRepository ledgerRepository,
            EventPublisher eventPublisher) {
        this.transactionManager = transactionManager;
        this.ledgerRepository = ledgerRepository;
        this.eventPublisher = eventPublisher;
    }

    @EventBreakerConsumer(eventType = "TransferRequested")
    public void handle(TransferRequested event) {
        transactionManager.begin();

        ledgerRepository.debit(event.getFromAccountId(), event.getAmount());
        ledgerRepository.credit(event.getToAccountId(), event.getAmount());

        transactionManager.commit();

        eventPublisher.publish(new TransferCompleted(event.getTransferId()));
    }
}
