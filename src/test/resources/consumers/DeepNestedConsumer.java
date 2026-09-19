package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

public class DeepNestedConsumer {

    private OrderRepository orderRepository;

    @EventBreakerConsumer(eventType = "OrderUpdated")
    public void handle(OrderUpdated event) {

        orderRepository.save(
                event.getOrder().withCustomer(
                        event.getCustomer().getId()
                )
        );
    }
}