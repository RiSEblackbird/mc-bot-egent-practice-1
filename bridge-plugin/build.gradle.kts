import com.github.jengelman.gradle.plugins.shadow.tasks.ShadowJar

plugins {
    java
    id("com.gradleup.shadow") version "8.3.3"
}

group = "com.example.bridge"
version = "0.1.0"

description = "Bridge plugin exposing Paper server data over HTTP for the mining bot"

java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(21))
    withSourcesJar()
}

repositories {
    mavenCentral()
    maven("https://repo.papermc.io/repository/maven-public/")
    // WorldGuard / WorldEdit 系ライブラリ用
    maven("https://maven.enginehub.org/repo/")
}

dependencies {
    compileOnly("io.papermc.paper:paper-api:1.21.1-R0.1-SNAPSHOT")
    compileOnly("com.sk89q.worldguard:worldguard-bukkit:7.0.12")
    compileOnly("com.sk89q.worldedit:worldedit-bukkit:7.3.9")
    implementation("com.fasterxml.jackson.core:jackson-databind:2.17.1")
    implementation("com.fasterxml.jackson.datatype:jackson-datatype-jsr310:2.17.1")
    compileOnly(files("libs/CoreProtect-22.0.jar"))
    testImplementation("io.papermc.paper:paper-api:1.21.1-R0.1-SNAPSHOT")
    testImplementation("com.sk89q.worldguard:worldguard-bukkit:7.0.12")
    testImplementation("com.sk89q.worldedit:worldedit-bukkit:7.3.9")
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
    testImplementation("org.mockito:mockito-core:5.11.0")
}

tasks.withType<JavaCompile>().configureEach {
    options.encoding = "UTF-8"
    options.release.set(21)
}

val shadowJar = tasks.named<ShadowJar>("shadowJar") {
    archiveClassifier.set("")
    mergeServiceFiles()
    minimize()
    // Docker Compose 環境向けにプラグイン依存（CoreProtect / WorldGuard / WorldEdit）を
    // build/libs へまとめてコピーする。単一ファイルマウントは Windows Docker Desktop で
    // 問題を起こすため、ビルド成果物と同じディレクトリに配置して一括マウントで済むようにする。
    doLast {
        copy {
            from("libs")
            include("*.jar")
            into(layout.buildDirectory.dir("libs"))
        }
    }
}

tasks.named("build") {
    dependsOn(shadowJar)
}

tasks.jar {
    archiveClassifier.set("slim")
}

tasks.test {
    useJUnitPlatform()
}
