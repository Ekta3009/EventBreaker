package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

public class ComplexConsumer {

    private OrderRepository orderRepository;
    private AuditService auditService;

    @EventBreakerConsumer(eventType = "OrderReceived")
    public void handle(OrderReceived event) {

        Order order = orderRepository.find(event.getOrderId());

        String status = order.getStatus();

        if (status.equals("READY")) {

            order.markProcessed();

            auditService.record(
                    event.getOrderId(),
                    status
            );

            OrderSummary summary =
                    new OrderSummary(
                            order.getId(),
                            order.getStatus()
                    );

            auditService.publishSummary(summary);
        }
    }
}