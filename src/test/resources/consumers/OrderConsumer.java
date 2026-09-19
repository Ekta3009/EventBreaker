package consumers;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;
import com.eventbreaker.event.OrderCreated;
import com.eventbreaker.event.OrderProcessed;
import com.eventbreaker.model.Order;
import com.eventbreaker.service.EventPublisher;
import com.eventbreaker.service.OrderRepository;
import com.eventbreaker.service.PaymentClient;

public class OrderConsumer {

    private final OrderRepository orderRepository;
    private final PaymentClient paymentClient;
    private final EventPublisher eventPublisher;

    public OrderConsumer(
            OrderRepository orderRepository,
            PaymentClient paymentClient,
            EventPublisher eventPublisher) {

        this.orderRepository = orderRepository;
        this.paymentClient = paymentClient;
        this.eventPublisher = eventPublisher;
    }

    @EventBreakerConsumer(eventType = "OrderCreated")
    public void consume(OrderCreated event) {

        Order order = orderRepository.get(event.getOrderId());

        paymentClient.charge(order.getId());

        order.markPaid();

        orderRepository.save(order);

        eventPublisher.publish(
                new OrderProcessed(order.getId())
        );
    }
}