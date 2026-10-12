package test;

import org.springframework.context.event.EventListener;

public class DeepNestedConsumer {

    private OrderRepository orderRepository;

    @EventListener
    public void handle(OrderUpdated event) {

        orderRepository.save(
                event.getOrder().withCustomer(
                        event.getCustomer().getId()
                )
        );
    }
}