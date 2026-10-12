package test;

import org.springframework.context.event.EventListener;

public class ConditionalConsumer {

    private OrderRepository orderRepository;
    private NotificationService notificationService;

    @EventListener
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