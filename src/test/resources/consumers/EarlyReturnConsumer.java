package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Real-world pattern — guard clauses with early returns.
 *
 * Several validation paths return early without side effects. Only the
 * final path issues a refund, and it does so without checking whether
 * this refund was already issued.
 */
public class EarlyReturnConsumer {

    private final AccountRepository accountRepository;
    private final RefundClient refundClient;
    private final AuditLogger auditLogger;

    public EarlyReturnConsumer(
            AccountRepository accountRepository,
            RefundClient refundClient,
            AuditLogger auditLogger) {
        this.accountRepository = accountRepository;
        this.refundClient = refundClient;
        this.auditLogger = auditLogger;
    }

    @EventBreakerConsumer(eventType = "RefundRequested")
    public void handle(RefundRequested event) {
        if (event.getAmount() <= 0) {
            return;
        }

        Account account = accountRepository.findById(event.getAccountId());
        if (account == null) {
            return;
        }

        if (account.isFrozen()) {
            auditLogger.log(event.getAccountId(), "REFUND_BLOCKED_FROZEN");
            return;
        }

        refundClient.issueRefund(account.getId(), event.getAmount());
        auditLogger.log(event.getAccountId(), "REFUND_ISSUED");
    }
}
