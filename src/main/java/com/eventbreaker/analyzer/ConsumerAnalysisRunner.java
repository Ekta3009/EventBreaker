package com.eventbreaker.analyzer;

import com.eventbreaker.analyzer.model.ConsumerAnalysis;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;

public class ConsumerAnalysisRunner {

    private final ConsumerAnalyzer analyzer;

    public ConsumerAnalysisRunner() {
        this.analyzer = new ConsumerAnalyzer();
    }

    public List<ConsumerAnalysis> analyzeDirectory(
            Path directory
    ) throws Exception {

        List<Path> consumerFiles =
                Files.walk(directory)
                        .filter(Files::isRegularFile)
                        .filter(path ->
                                path.toString().endsWith(".java")
                        )
                        .sorted(
                                Comparator.comparing(
                                        Path::toString
                                )
                        )
                        .toList();

        return consumerFiles.stream()
                .map(this::analyzeFile)
                .toList();
    }

    private ConsumerAnalysis analyzeFile(
            Path consumerFile
    ) {

        try {
            return analyzer.analyze(consumerFile);
        } catch (Exception e) {
            throw new IllegalArgumentException(
                    "Failed to analyze consumer: "
                            + consumerFile,
                    e
            );
        }
    }
}