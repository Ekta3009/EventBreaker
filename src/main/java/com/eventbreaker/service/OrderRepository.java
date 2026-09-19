package com.eventbreaker.service;
import com.eventbreaker.model.Order;

import java.util.HashMap;
import java.util.Map;

public class OrderRepository {
    private final Map<String, Order> orders = new HashMap<>();

    public Order get(String orderId) {
        return orders.get(orderId);
    }

    public void save(Order order) {
        orders.put(order.getId(), order);
    }
}
