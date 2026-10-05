package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Safe consumer — read-only query handling.
 *
 * This consumer only reads data; it never writes, saves, charges,
 * or sends anything. Receiving the same event twice produces the
 * same read result with zero side effects, so duplicate delivery
 * is completely harmless.
 */
public class ReadOnlyConsumer {

    private OrderRepository orderRepository;
    private CustomerRepository customerRepository;

    @EventBreakerConsumer(eventType = "OrderSummaryRequested")
    public void handle(OrderSummaryRequested event) {
        Order order = orderRepository.findById(event.getOrderId());
        Customer customer = customerRepository.findById(order.getCustomerId());
        // Reads only — no external state is modified.
        // Calling this handler multiple times is safe.
    }
}
