package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

public class ConditionalConsumer {

    private OrderRepository orderRepository;
    private NotificationService notificationService;

    @EventBreakerConsumer(eventType = "OrderCreated")
    public void handle(OrderCreated event) {

        if (event.isPriority()) {

            orderRepository.markPriority(event.getOrderId());

            notificationService.sendPriorityNotification(
                    event.getOrderId()
            );

        } else {

            orderRepository.markStandard(event.getOrderId());
        }
    }
}