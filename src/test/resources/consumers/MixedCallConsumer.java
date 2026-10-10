package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

import java.util.List;

/**
 * Stress test — void calls, return-value calls (object, primitive,
 * boolean, String, List) and chained calls in a single handler.
 */
public class MixedCallConsumer {

    private final CustomerService customerService;
    private final LoyaltyService loyaltyService;
    private final EmailClient emailClient;
    private final RetryQueue retryQueue;

    public MixedCallConsumer(
            CustomerService customerService,
            LoyaltyService loyaltyService,
            EmailClient emailClient,
            RetryQueue retryQueue) {
        this.customerService = customerService;
        this.loyaltyService = loyaltyService;
        this.emailClient = emailClient;
        this.retryQueue = retryQueue;
    }

    @EventBreakerConsumer(eventType = "PurchaseCompleted")
    public void handle(PurchaseCompleted event) {
        String email = customerService.getCustomer(event.getCustomerId())
                .getContactInfo()
                .getEmail();

        int points = loyaltyService.calculatePoints(event.getAmount());
        loyaltyService.addPoints(event.getCustomerId(), points);

        List<String> perks = loyaltyService.perksFor(event.getCustomerId());
        String body = "You earned " + points + " points. Perks: " + String.join(", ", perks);

        boolean sent = emailClient.send(email, body);
        if (!sent) {
            retryQueue.enqueue(event.getPurchaseId());
        }
    }
}
