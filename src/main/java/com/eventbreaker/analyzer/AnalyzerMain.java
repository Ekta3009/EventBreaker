package com.eventbreaker.analyzer;

import com.eventbreaker.analyzer.model.ConsumerAnalysis;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import java.nio.file.Path;
import java.util.Map;

public class AnalyzerMain {

    public static void main(String[] args) {

        if (args.length != 1) {
            writeError("Usage: AnalyzerMain <consumer-file.java>");
            System.exit(1);
        }

        Path sourceFile = Path.of(args[0]);

        if (!sourceFile.toFile().exists()) {
            writeError("File not found: " + args[0]);
            System.exit(1);
        }

        ObjectMapper mapper = new ObjectMapper();
        mapper.setSerializationInclusion(JsonInclude.Include.NON_NULL);
        mapper.enable(SerializationFeature.INDENT_OUTPUT);

        try {
            ConsumerAnalyzer analyzer = new ConsumerAnalyzer();
            ConsumerAnalysis analysis = analyzer.analyze(sourceFile);
            System.out.println(mapper.writeValueAsString(analysis));
        } catch (Exception e) {
            try {
                System.out.println(
                        mapper.writeValueAsString(
                                Map.of("error", e.getMessage())
                        )
                );
            } catch (Exception ignored) {
                System.out.println("{\"error\": \"Analysis failed\"}");
            }
            System.exit(1);
        }
    }

    private static void writeError(String message) {
        System.out.println("{\"error\": \"" + message + "\"}");
    }
}
